"""Génère des données SYNTHÉTIQUES (marche aléatoire) pour tester le pipeline.

ATTENTION : ces données n'ont aucun edge réel. Un résultat positif ici ne prouve rien.
Usage : python tools/make_synthetic.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data"


def gen(rng, name, start, s0, vol, n=35000):
    idx = pd.date_range(start, periods=n, freq="15min")
    close = s0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    o = np.r_[close[0], close[:-1]]
    hi = np.maximum(o, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    df = pd.DataFrame({"time": idx.strftime("%Y-%m-%d %H:%M:%S"), "open": o.round(5), "high": hi.round(5),
                       "low": lo.round(5), "close": close.round(5), "spread": rng.integers(8, 14, n)})
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / f"SYNTH_{name}_M15.csv", index=False)


if __name__ == "__main__":
    rng = np.random.default_rng(123)
    gen(rng, "EURUSD", "2019-01-02 00:00", 1.10, 0.0004)
    gen(rng, "GBPUSD", "2019-01-02 00:00", 1.28, 0.0005)
    gen(rng, "US500", "2019-01-02 00:00", 2500, 0.0006)
    print("Données synthétiques écrites dans", OUT)
