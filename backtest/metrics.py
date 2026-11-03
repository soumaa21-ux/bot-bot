"""Métriques de performance, calculées sur les trades nets (commissions et spread inclus)."""
import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame, equity: pd.Series, initial: float, failed: dict | None = None) -> dict:
    out = {"trades": 0}
    if trades is None or len(trades) == 0:
        out.update(net_profit=0.0, return_pct=0.0, win_rate=0.0, profit_factor=0.0, expectancy_R=0.0,
                   max_dd_pct=_max_dd(equity, initial), worst_day_pct=_worst_day(equity, initial),
                   longest_losing_streak=0, failed=failed)
        return out
    pnl = trades["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    gross_loss = -losses.sum()
    out.update(
        trades=int(len(trades)),
        net_profit=float(pnl.sum()),
        return_pct=float(pnl.sum() / initial * 100),
        win_rate=float((pnl > 0).mean() * 100),
        profit_factor=float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
        expectancy_R=float(trades["R"].mean()),
        avg_win_R=float(trades.loc[pnl > 0, "R"].mean()) if len(wins) else 0.0,
        avg_loss_R=float(trades.loc[pnl <= 0, "R"].mean()) if len(losses) else 0.0,
        max_dd_pct=_max_dd(equity, initial),
        worst_day_pct=_worst_day(equity, initial),
        longest_losing_streak=_longest_streak(pnl.values <= 0),
        exit_reasons=trades["reason"].value_counts().to_dict(),
        per_symbol={k: float(v) for k, v in trades.groupby("symbol")["pnl"].sum().items()},
        failed=failed,
    )
    return out


def _max_dd(equity: pd.Series, initial: float) -> float:
    if equity is None or len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    dd = (peak - equity).max()
    return float(dd / initial * 100)


def _worst_day(equity: pd.Series, initial: float) -> float:
    """Pire variation intrajournalière de l'équité, en % du capital initial (approximation par jour civil UTC)."""
    if equity is None or len(equity) == 0:
        return 0.0
    worst = 0.0
    for _, day in equity.groupby(equity.index.date):
        start = day.iloc[0]
        worst = min(worst, (day - start).min())
    return float(worst / initial * 100)


def _longest_streak(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def format_report(title: str, s: dict) -> str:
    lines = [f"## {title}", ""]
    rows = [
        ("Trades", s.get("trades")),
        ("Profit net", f"{s.get('net_profit', 0):+.2f}"),
        ("Rendement", f"{s.get('return_pct', 0):+.2f} %"),
        ("Winrate", f"{s.get('win_rate', 0):.1f} %"),
        ("Profit factor (net)", f"{s.get('profit_factor', 0):.2f}"),
        ("Espérance (R)", f"{s.get('expectancy_R', 0):+.3f}"),
        ("Drawdown max (% du capital initial)", f"{s.get('max_dd_pct', 0):.2f} %"),
        ("Pire journée (% du capital initial)", f"{s.get('worst_day_pct', 0):.2f} %"),
        ("Plus longue série de pertes", s.get("longest_losing_streak")),
        ("Résultat par symbole", s.get("per_symbol")),
        ("Sorties", s.get("exit_reasons")),
        ("Échec firme", s.get("failed")),
    ]
    lines += ["| Indicateur | Valeur |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines) + "\n"


def equity_from_trades(trades: pd.DataFrame, initial: float) -> pd.Series:
    """Courbe d'équité reconstruite à la clôture des trades (pour les résultats hors échantillon)."""
    if trades is None or trades.empty:
        return pd.Series([initial], index=pd.DatetimeIndex([pd.Timestamp("1970-01-01")]), name="equity")
    t = trades.sort_values("close_time")
    eq = initial + t["pnl"].cumsum()
    return pd.Series(eq.values, index=pd.DatetimeIndex(t["close_time"]), name="equity")
