"""Walk-forward avec holdout final.

Découpage chronologique :
  [ ----------- 80 % : optimisation + walk-forward ----------- | 20 % : HOLDOUT jamais touché ]

Mode recherche par défaut (enforce=False) : les trades sont mesurés sans que les règles de la firme
gèlent le compte, sinon un drawdown précoce masque toute la suite de l'échantillon.

Pour chaque fenêtre d'entraînement, on choisit les paramètres de la grille qui maximisent
le profit factor NET sur les trades ouverts dans cette fenêtre (minimum de trades requis).
Ces paramètres sont appliqués à la fenêtre de test suivante. Les trades hors échantillon
sont concaténés et évalués. Le holdout n'est évalué qu'une seule fois, avec les paramètres
retenus sur la période d'optimisation.
"""
import itertools
from dataclasses import dataclass

import pandas as pd

from backtest.engine import PortfolioBacktest
from backtest.metrics import summarize

DEFAULT_GRID = {
    "sl_atr_mult": [1.5, 2.0],
    "tp_rr": [1.5, 2.0],
    "adx_min": [20, 25],
}
MIN_TRADES_TRAIN = 20


@dataclass
class WFResult:
    oos_trades: pd.DataFrame
    chosen: list
    holdout: dict | None
    holdout_trades: pd.DataFrame | None


def _grid(grid: dict):
    keys = list(grid)
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def _pf(trades: pd.DataFrame) -> float:
    if trades is None or trades.empty:
        return 0.0
    win = trades.loc[trades.pnl > 0, "pnl"].sum()
    loss = -trades.loc[trades.pnl <= 0, "pnl"].sum()
    return float(win / loss) if loss > 0 else (float("inf") if win > 0 else 0.0)


def _run_params(data, settings, rules, news, params, balance, enforce=False):
    bt = PortfolioBacktest(data, settings, rules, news=news, strategy_params=params,
                           enforce_firm_limits=enforce)
    return bt.run(balance)


def walk_forward(data: dict, settings: dict, rules: dict, news=None, grid: dict | None = None,
                 n_folds: int = 4, balance: float = 100000.0, holdout_frac: float = 0.2,
                 min_trades: int = MIN_TRADES_TRAIN, enforce: bool = False) -> WFResult:
    grid = grid or DEFAULT_GRID
    # bornes temporelles communes
    start = min(df.index.min() for df, _ in data.values())
    end = max(df.index.max() for df, _ in data.values())
    cut = start + (end - start) * (1 - holdout_frac)
    opt_data = {k: (df[df.index < cut], spec) for k, (df, spec) in data.items()}
    hold_data = {k: (df[df.index >= cut], spec) for k, (df, spec) in data.items()}

    edges = pd.date_range(start, cut, periods=n_folds + 1)
    # résultats de chaque combinaison sur tout l'échantillon d'optimisation (une seule passe par combinaison)
    results = {}
    for combo in _grid(grid):
        results[tuple(combo.items())] = _run_params(opt_data, settings, rules, news, combo, balance, enforce).trades

    oos, chosen = [], []
    for k in range(n_folds - 1):  # la dernière fenêtre sert de test pour le dernier entraînement
        tr_start, tr_end = edges[0], edges[k + 1]
        te_start, te_end = edges[k + 1], edges[k + 2]
        best_key, best_score = None, -1.0
        for key, trades in results.items():
            if trades is None or trades.empty:
                continue
            tr = trades[(trades.open_time >= tr_start) & (trades.open_time < tr_end)]
            if len(tr) < min_trades:
                continue
            score = _pf(tr)
            if score > best_score:
                best_key, best_score = key, score
        if best_key is None:
            chosen.append({"fold": k, "params": None})
            continue
        chosen.append({"fold": k, "params": dict(best_key), "train_pf": round(best_score, 3)})
        t = results[best_key]
        oos.append(t[(t.open_time >= te_start) & (t.open_time < te_end)])

    oos_trades = pd.concat(oos, ignore_index=True) if oos else pd.DataFrame()

    # Holdout : paramètres retenus sur la fenêtre la plus récente d'optimisation, évalués une seule fois
    holdout, holdout_trades = None, None
    last = chosen[-1] if chosen else None
    if last and last.get("params"):
        res = _run_params(hold_data, settings, rules, news, last["params"], balance, enforce)
        holdout_trades = res.trades
        holdout = summarize(res.trades, res.equity, balance, res.failed)
    return WFResult(oos_trades=oos_trades, chosen=chosen, holdout=holdout, holdout_trades=holdout_trades)
