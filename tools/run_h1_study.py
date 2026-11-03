"""Étude H1 en deux volets distincts.

VOLET A - EDGE (mode recherche, enforce_firm_limits=False) :
    les trades sont mesurés sans que les règles de la firme gèlent le compte.
    Walk-forward + holdout, par symbole et sur le portefeuille, stress des coûts, Monte Carlo.

VOLET B - AURAIS-JE PASSÉ LE CHALLENGE ? (enforce_firm_limits=True) :
    compte réel simulé avec toutes les règles (blocages, fermeture forcée, rupture).
    Plusieurs dates de départ, horizon fixe ; on mesure la probabilité de rupture.

Usage : python tools/run_h1_study.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.data import load_csv  # noqa: E402
from backtest.engine import PortfolioBacktest  # noqa: E402
from backtest.metrics import equity_from_trades, format_report, summarize  # noqa: E402
from backtest.monte_carlo import monte_carlo  # noqa: E402
from backtest.walk_forward import walk_forward  # noqa: E402
from core.config_loader import load_rules, load_settings, load_symbols  # noqa: E402
from core.risk_engine import SymbolSpec  # noqa: E402
from core.state_store import StateStore  # noqa: E402

BALANCE = 100000.0
N_FOLDS = 3
MIN_TRADES_SYMBOL = 8
MIN_TRADES_PORTFOLIO = 20
CHALLENGE_HORIZON_DAYS = 90
CHALLENGE_STEP_DAYS = 45


def load_all(specs):
    data, skipped = {}, {}
    for f in sorted((ROOT / "data").glob("*_H1.csv")):
        sym = f.stem.replace("_H1", "")
        sp = specs.get(sym)
        if sp is None:
            skipped[sym] = "absent de symbols.yaml"
            continue
        if sp.get("excluded") or not sp.get("quote_is_usd", False):
            skipped[sym] = sp.get("note", "devise non USD")
            continue
        data[sym] = (load_csv(str(f)), SymbolSpec.from_dict(sym, sp))
    return data, skipped


def stressed(settings):
    c = settings["costs"]
    return {**settings, "costs": {**c, "slippage_pips": c["slippage_pips"] * 2,
                                  "default_spread_pips": c["default_spread_pips"] * 2}}


def chosen_str(wf):
    return "; ".join(f"f{c['fold']}={c.get('params')}" for c in wf.chosen)


def volet_a(data, settings, rules, lines):
    lines += ["# VOLET A : EDGE (mode recherche)", "",
              "Trades mesurés sans blocage de la firme. Coûts inclus. Hors échantillon = fenêtres de test.", ""]
    rows = []
    for sym in sorted(data):
        wf = walk_forward({sym: data[sym]}, settings, rules, n_folds=N_FOLDS, balance=BALANCE,
                          min_trades=MIN_TRADES_SYMBOL, enforce=False)
        oos = summarize(wf.oos_trades, equity_from_trades(wf.oos_trades, BALANCE), BALANCE)
        h = wf.holdout or {}
        rows.append((sym, oos.get("trades", 0), oos.get("profit_factor", 0), oos.get("expectancy_R", 0),
                     h.get("trades", 0), h.get("profit_factor", 0), h.get("expectancy_R", 0)))
        lines.append(format_report(f"{sym} : walk-forward hors échantillon (recherche)", oos))
        lines.append("Paramètres retenus : " + chosen_str(wf) + "\n")
        if wf.holdout:
            lines.append(format_report(f"{sym} : holdout (évalué une fois)", wf.holdout))

    if len(data) > 1:
        wf = walk_forward(data, settings, rules, n_folds=N_FOLDS, balance=BALANCE,
                          min_trades=MIN_TRADES_PORTFOLIO, enforce=False)
        oos = summarize(wf.oos_trades, equity_from_trades(wf.oos_trades, BALANCE), BALANCE)
        lines.append(format_report("PORTEFEUILLE : walk-forward hors échantillon (recherche)", oos))
        lines.append("Paramètres retenus : " + chosen_str(wf) + "\n")
        if wf.holdout:
            lines.append(format_report("PORTEFEUILLE : holdout (évalué une fois)", wf.holdout))
        if not wf.oos_trades.empty:
            mc = monte_carlo(wf.oos_trades["R"].values, settings["risk"]["risk_per_trade_pct"], BALANCE,
                             trailing_limit_pct=rules["max_trailing_dd_pct"])
            lines += ["## Monte Carlo (portefeuille, trades hors échantillon)", ""]
            lines += [f"- {k} : {v}" for k, v in mc.items()] + [""]
        wf_s = walk_forward(data, stressed(settings), rules, n_folds=N_FOLDS, balance=BALANCE,
                            min_trades=MIN_TRADES_PORTFOLIO, enforce=False)
        oss = summarize(wf_s.oos_trades, equity_from_trades(wf_s.oos_trades, BALANCE), BALANCE)
        lines.append(format_report("PORTEFEUILLE : walk-forward, coûts x2 (recherche)", oss))
    lines += ["## Synthèse par symbole (edge)", "",
              "| Symbole | Trades OOS | PF OOS | Espérance R OOS | Trades holdout | PF holdout | Espérance R holdout |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]:.2f} | {r[3]:+.3f} | {r[4]} | {r[5]:.2f} | {r[6]:+.3f} |")
    lines.append("")


def volet_b(data, settings, rules, lines):
    lines += ["# VOLET B : AURAIS-JE PASSÉ LE CHALLENGE ?", "",
              f"Compte réel simulé ({BALANCE:,.0f}) avec toutes les règles de la firme. Départs tous les "
              f"{CHALLENGE_STEP_DAYS} jours, horizon {CHALLENGE_HORIZON_DAYS} jours. Chaque départ repart d'un compte "
              "neuf (mêmes paramètres par défaut). Les 200 premières bougies de chaque fenêtre servent de chauffe "
              "pour les indicateurs.", ""]
    start = min(df.index.min() for df, _ in data.values())
    end = max(df.index.max() for df, _ in data.values())
    results = []
    cur = start + timedelta(days=10)
    while cur + timedelta(days=CHALLENGE_HORIZON_DAYS) <= end:
        stop = cur + timedelta(days=CHALLENGE_HORIZON_DAYS)
        sub = {}
        for sym, (df, spec) in data.items():
            d = df[(df.index >= cur - timedelta(days=20)) & (df.index < stop)]
            if len(d) > 250:
                sub[sym] = (d, spec)
        if sub:
            res = PortfolioBacktest(sub, settings, rules, store=StateStore(), enforce_firm_limits=True).run(BALANCE)
            s = summarize(res.trades, res.equity, BALANCE, res.failed)
            # ne garder que les trades ouverts pendant la fenêtre de jeu
            results.append(dict(start=cur.date(), failed=res.failed is not None,
                                ret=s["return_pct"], dd=s["max_dd_pct"], trades=s["trades"],
                                reason=(res.failed or {}).get("reason", "-")))
        cur += timedelta(days=CHALLENGE_STEP_DAYS)

    n = len(results)
    if n == 0:
        lines.append("Pas assez de données pour une simulation de challenge.\n")
        return
    fails = sum(r["failed"] for r in results)
    rets = [r["ret"] for r in results]
    lines += ["| Indicateur | Valeur |", "|---|---|",
              f"| Départs simulés | {n} |",
              f"| Ruptures (règle firme) | {fails} ({fails / n * 100:.0f} %) |",
              f"| Rendement médian sur l'horizon | {sorted(rets)[n // 2]:+.2f} % |",
              f"| Rendement min / max | {min(rets):+.2f} % / {max(rets):+.2f} % |",
              f"| Drawdown max médian | {sorted(r['dd'] for r in results)[n // 2]:.2f} % |", ""]
    lines += ["| Départ | Rupture | Rendement % | DD max % | Trades |", "|---|---|---|---|---|"]
    lines += [f"| {r['start']} | {r['reason']} | {r['ret']:+.2f} | {r['dd']:.2f} | {r['trades']} |" for r in results]
    lines.append("")


def main():
    settings, rules, specs = load_settings(), load_rules(), load_symbols()
    data, skipped = load_all(specs)
    lines = [f"# Étude H1 : {datetime.now():%Y-%m-%d %H:%M}", "", "## Périmètre", ""]
    lines += [f"- Testé : {', '.join(sorted(data))}"]
    lines += [f"- Exclu : {', '.join(f'{k} ({v})' for k, v in skipped.items())}", ""]
    for k, (df, _) in sorted(data.items()):
        lines.append(f"- {k} : {len(df)} bougies H1, {df.index.min():%Y-%m-%d} -> {df.index.max():%Y-%m-%d}")
    lines.append("")
    volet_a(data, settings, rules, lines)
    volet_b(data, settings, rules, lines)

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"study_h1_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Rapport : {path}")


if __name__ == "__main__":
    main()
