"""Risque intraday (équité flottante) sur les données H1 existantes.

Rejoue le moteur en mode recherche (aucun blocage de la firme) pour mesurer le pire
creux flottant : perte journalière depuis le solde de début de journée et trailing
depuis le plus haut. Le portefeuille est testé en compte partagé (un seul compte).

Usage : python tools/intraday_risk_study.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_h1_study import load_all, BALANCE  # noqa: E402
from backtest.engine import PortfolioBacktest  # noqa: E402
from core.config_loader import load_rules, load_settings, load_symbols  # noqa: E402
from core.state_store import StateStore  # noqa: E402

DAILY_LIMIT = 0.03
TRAIL_LIMIT = 0.06


def measure(data, settings, rules):
    bt = PortfolioBacktest(data, settings, rules, store=StateStore(), enforce_firm_limits=False)
    rec = []
    orig = bt.risk.update

    def spy(now, balance, equity):
        rec.append((now, balance, equity))
        return orig(now, balance, equity)

    bt.risk.update = spy
    res = bt.run(BALANCE)

    # Perte journalière flottante : équité au pire point vs solde de début de journée
    days = {}
    hwm, trail_worst, trail_when = BALANCE, 0.0, None
    first_daily_breach, first_trail_breach = None, None
    for now, bal, eq in rec:
        day = bt.risk.trading_day(now)
        if day not in days:
            days[day] = {"ref": bal, "worst": 0.0, "when": None}
        dd = (eq - days[day]["ref"]) / BALANCE
        if dd < days[day]["worst"]:
            days[day]["worst"], days[day]["when"] = dd, now
        if dd <= -DAILY_LIMIT and first_daily_breach is None:
            first_daily_breach = now
        hwm = max(hwm, bal)
        tr = (eq - hwm) / hwm
        if tr < trail_worst:
            trail_worst, trail_when = tr, now
        if tr <= -TRAIL_LIMIT and first_trail_breach is None:
            first_trail_breach = now

    worst_day = min(days.values(), key=lambda d: d["worst"]) if days else None
    n_daily_breach = sum(1 for d in days.values() if d["worst"] <= -DAILY_LIMIT)
    n_trail_breach = 1 if trail_worst <= -TRAIL_LIMIT else 0
    return {
        "trades": len(res.trades),
        "days": len(days),
        "worst_daily_float": worst_day["worst"] if worst_day else 0.0,
        "worst_daily_when": str(worst_day["when"]) if worst_day else "-",
        "days_breach_3pct": n_daily_breach,
        "worst_trailing_float": trail_worst,
        "worst_trailing_when": str(trail_when),
        "trailing_breach": n_trail_breach,
        "first_daily_breach": str(first_daily_breach) if first_daily_breach else "-",
        "first_trail_breach": str(first_trail_breach) if first_trail_breach else "-",
        "final_balance": res.final_balance,
    }


def main():
    settings, rules, specs = load_settings(), load_rules(), load_symbols()
    data, skipped = load_all(specs)
    wanted = set(settings["symbols"])
    data = {k: v for k, v in data.items() if k in wanted}
    print("Symboles testés :", sorted(data))
    print("Exclus :", skipped)
    print()
    print(f"{'symbole':10s} {'trades':>6s} {'pire jour flott.':>17s} {'jours>=3%':>10s} {'pire trailing':>14s} {'breach 6%':>10s}")
    for sym in sorted(data):
        r = measure({sym: data[sym]}, settings, rules)
        print(f"{sym:10s} {r['trades']:6d} {r['worst_daily_float']*100:16.2f}% {r['days_breach_3pct']:10d} {r['worst_trailing_float']*100:13.2f}% {r['trailing_breach']:10d}")
    r = measure(data, settings, rules)
    print(f"{'PORTEF.':10s} {r['trades']:6d} {r['worst_daily_float']*100:16.2f}% {r['days_breach_3pct']:10d} {r['worst_trailing_float']*100:13.2f}% {r['trailing_breach']:10d}")
    print()
    print("Portefeuille : pire jour flottant le", r["worst_daily_when"], "| pire trailing le", r["worst_trailing_when"])
    print("Premier franchissement 3 % journalier (flottant) :", r["first_daily_breach"])
    print("Premier franchissement 6 % trailing (flottant) :", r["first_trail_breach"])
    print("Solde final (sans blocage) :", round(r["final_balance"], 2))


if __name__ == "__main__":
    main()
