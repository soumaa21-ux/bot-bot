"""CLI de backtest.

Exemples :
  python -m backtest.run_backtest --csv data/EURUSD_M15.csv --symbol EURUSD
  python -m backtest.run_backtest --csv data/EURUSD_M15.csv --symbol EURUSD --walk-forward
  python -m backtest.run_backtest --csv data/EURUSD_M1.csv --symbol EURUSD --resample 15min --walk-forward \
        --news data/news.csv
Plusieurs symboles : --csv EURUSD=data/eurusd.csv --csv GBPUSD=data/gbpusd.csv --walk-forward

Le rapport est écrit dans reports/. Le holdout n'est évalué qu'avec --walk-forward.
"""
import argparse
from datetime import datetime
from pathlib import Path

from backtest.data import load_csv, resample
from backtest.engine import PortfolioBacktest
from backtest.metrics import equity_from_trades, format_report, summarize
from backtest.monte_carlo import monte_carlo
from backtest.walk_forward import walk_forward
from core.config_loader import ROOT, load_rules, load_settings, load_symbols
from core.filters import load_news_calendar
from core.risk_engine import SymbolSpec
from core.state_store import StateStore


def build_data(pairs: list[str], resample_rule: str | None, specs: dict):
    data = {}
    for item in pairs:
        if "=" in item:
            sym, path = item.split("=", 1)
        else:
            sym, path = item.upper().replace(".CSV", ""), item
            sym = Path(path).stem.split("_")[0].upper()
        if sym not in specs:
            raise SystemExit(f"Symbole inconnu dans symbols.yaml : {sym}")
        spec = SymbolSpec.from_dict(sym, specs[sym])
        df = load_csv(path, point=spec.tick_size)
        if resample_rule:
            df = resample(df, resample_rule)
        data[sym] = (df, spec)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", required=True,
                    help="fichier CSV, ou SYMBOLE=fichier (répétable)")
    ap.add_argument("--symbol", help="symbole si --csv est un chemin simple")
    ap.add_argument("--balance", type=float, default=100000)
    ap.add_argument("--resample", help="ex. 15min pour agréger des données M1")
    ap.add_argument("--news", help="CSV d'annonces à fort impact (colonne time, UTC)")
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--montecarlo", action="store_true", help="Monte Carlo sur les trades OOS")
    ap.add_argument("--out", default=str(ROOT / "reports"))
    args = ap.parse_args()

    settings, rules, specs = load_settings(), load_rules(), load_symbols()
    pairs = args.csv
    if args.symbol and len(pairs) == 1 and "=" not in pairs[0]:
        pairs = [f"{args.symbol}={pairs[0]}"]
    data = build_data(pairs, args.resample, specs)
    news = load_news_calendar(args.news) if args.news else None
    lines = [f"# Rapport de backtest : {', '.join(sorted(data))}",
             f"_Généré le {datetime.now().isoformat(timespec='seconds')}_", ""]
    for k, (df, _) in data.items():
        lines.append(f"- {k} : {len(df)} bougies, du {df.index.min()} au {df.index.max()}")
    lines.append("")
    lines.append(f"Limites : journalière {rules['max_daily_loss_pct']:.0%}, trailing {rules['max_trailing_dd_pct']:.0%}, "
                 f"capital {args.balance:,.0f}.")
    lines.append("")

    if args.walk_forward:
        wf = walk_forward(data, settings, rules, news=news, balance=args.balance)
        lines.append("## Walk-forward (hors échantillon)")
        lines.append("")
        for c in wf.chosen:
            lines.append(f"- Fenêtre {c['fold']} : {c.get('params')} (PF entraînement {c.get('train_pf', '-')})")
        lines.append("")
        oos = summarize(wf.oos_trades, equity_from_trades(wf.oos_trades, args.balance), args.balance)
        lines.append(format_report("Résultats hors échantillon (walk-forward)", oos))
        if wf.holdout is not None:
            lines.append(format_report("Holdout final (évalué une seule fois)", wf.holdout))
        if args.montecarlo and not wf.oos_trades.empty:
            mc = monte_carlo(wf.oos_trades["R"].values, settings["risk"]["risk_per_trade_pct"],
                             args.balance, trailing_limit_pct=rules["max_trailing_dd_pct"],
                             target_pct=None)
            lines.append("## Monte Carlo (trades hors échantillon)")
            lines.append("")
            for k, v in mc.items():
                lines.append(f"- {k} : {v}")
            lines.append("")
    else:
        res = PortfolioBacktest(data, settings, rules, news=news, store=StateStore()).run(args.balance)
        s = summarize(res.trades, res.equity, args.balance, res.failed)
        lines.append(format_report("Backtest (paramètres par défaut, en échantillon)", s))
        lines.append("> Ce résultat est **en échantillon** : il ne prouve rien. Utilisez --walk-forward.")
        # Stress des coûts : spread, slippage et commission doublés. Une stratégie sans marge s'effondre ici.
        stressed = {**settings, "costs": {**settings["costs"],
                                          "slippage_pips": settings["costs"]["slippage_pips"] * 2,
                                          "commission_per_lot_round_turn":
                                              settings["costs"]["commission_per_lot_round_turn"] * 2,
                                          "default_spread_pips": settings["costs"]["default_spread_pips"] * 2}}
        res2 = PortfolioBacktest(data, stressed, rules, news=news, store=StateStore()).run(args.balance)
        s2 = summarize(res2.trades, res2.equity, args.balance, res2.failed)
        lines.append(format_report("Stress des coûts (x2)", s2))
        if args.montecarlo and not res.trades.empty:
            mc = monte_carlo(res.trades["R"].values, settings["risk"]["risk_per_trade_pct"], args.balance,
                             trailing_limit_pct=rules["max_trailing_dd_pct"])
            lines.append("## Monte Carlo")
            for k, v in mc.items():
                lines.append(f"- {k} : {v}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"backtest_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nRapport écrit : {path}")


if __name__ == "__main__":
    main()
