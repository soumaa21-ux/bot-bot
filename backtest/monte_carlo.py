"""Monte Carlo sur la séquence des trades : distribution du drawdown et probabilité de rupture.

Chaque trade est un multiple de R (gain/perte en unités de risque). On mélange l'ordre des trades
N fois, on rejoue l'équité avec le risque par trade, et on mesure :
  - probabilité de dépasser la limite de drawdown trailing ;
  - probabilité de dépasser la limite journalière (approximée par trade : une perte > limite en un trade) ;
  - distribution du drawdown max (p50, p95).
Note : le rendement final est identique quel que soit l'ordre (produit / somme commutatifs) :
il n'est donc pas rapporté. Seul le chemin (drawdown) dépend de l'ordre.
"""
import numpy as np


def monte_carlo(r_multiples, risk_pct: float, initial: float = 100000.0, n: int = 5000,
                trailing_limit_pct: float = 0.06, target_pct: float | None = None,
                seed: int = 42) -> dict:
    r = np.asarray(r_multiples, dtype=float)
    if r.size == 0:
        return {"n_trades": 0}
    rng = np.random.default_rng(seed)
    breach = 0
    hit_target = 0
    max_dds = []
    for _ in range(n):
        seq = rng.permutation(r)
        equity = initial * np.cumprod(1 + seq * risk_pct)
        peak = np.maximum.accumulate(np.r_[initial, equity])[1:]
        dd = (peak - equity) / initial
        max_dd = float(dd.max()) if dd.size else 0.0
        max_dds.append(max_dd)
        if max_dd >= trailing_limit_pct:
            breach += 1
        if target_pct is not None and np.any(equity >= initial * (1 + target_pct)):
            hit_target += 1
    max_dds = np.array(max_dds)
    return {
        "n_trades": int(r.size),
        "simulations": n,
        "p_trailing_breach": breach / n,
        "p_target_hit": (hit_target / n) if target_pct is not None else None,
        "max_dd_p50": float(np.percentile(max_dds, 50)),
        "max_dd_p95": float(np.percentile(max_dds, 95)),
    }
