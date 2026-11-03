"""Compare les variantes de protection du compte sur les données H1 existantes (mode réel)."""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from run_h1_study import load_all, BALANCE  # noqa: E402
from backtest.engine import PortfolioBacktest  # noqa: E402
from core.config_loader import load_rules, load_settings, load_symbols  # noqa: E402
from core.state_store import StateStore  # noqa: E402


def variant(settings, rules, budgets=None, rearm=True):
    s = copy.deepcopy(settings)
    r = copy.deepcopy(rules)
    s["risk"]["market_budgets"] = budgets or {}
    r["trailing_rearm"]["enabled"] = rearm
    return s, r


def run(data, s, r):
    st = StateStore()
    bt = PortfolioBacktest(data, s, r, store=st, enforce_firm_limits=True)
    res = bt.run(BALANCE)
    ev = st.get("trailing_lock_count") or 0
    dead = bool(st.get("trailing_dead"))
    t = res.trades
    mk = t[t.reason == "MARKET_BUDGET"].symbol.tolist() if len(t) else []
    return dict(trades=len(t), final=res.final_balance, rupture=res.failed is not None,
                locks=ev, dead=dead, market_locks=mk)


def main():
    settings, rules, specs = load_settings(), load_rules(), load_symbols()
    data, _ = load_all(specs)
    data = {k: v for k, v in data.items() if k in settings["symbols"]}
    idx = {k: 0.015 for k in ["US500", "US100", "US30"]}
    other = {k: 0.009 for k in ["EURUSD", "XAUUSD", "XAGUSD", "USOIL"]}
    weighted = {**idx, **other}
    equal = {k: 0.012 for k in data}
    cases = [
        ("1. Actuel : verrou définitif, pas de reprise", variant(settings, rules, None, rearm=False)),
        ("2. B : reprise à taille réduite (2 coupures max)", variant(settings, rules, None, rearm=True)),
        ("3. C : budgets égaux 1,2 %", variant(settings, rules, equal, rearm=False)),
        ("4. C pondéré : indices 1,5 %, autres 0,9 %", variant(settings, rules, weighted, rearm=False)),
        ("5. C pondéré + B (recommandé)", variant(settings, rules, weighted, rearm=True)),
        ("6. C égal + B", variant(settings, rules, equal, rearm=True)),
    ]
    print(f"{'variante':52s} {'trades':>6s} {'solde final':>12s} {'rupture':>8s} {'coupures':>9s} {'marchés gelés'}")
    for name, (s, r) in cases:
        o = run(data, s, r)
        print(f"{name:52s} {o['trades']:6d} {o['final']:12.0f} {str(o['rupture']):>8s} {o['locks']:9d} {','.join(o['market_locks']) or '-'}{' (mort)' if o['dead'] else ''}")


if __name__ == "__main__":
    main()
