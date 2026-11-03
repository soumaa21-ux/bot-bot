import numpy as np
import pandas as pd

from core.strategy import MACrossRSITrend, StrategyParams


def trend_df(n=600, drift=0.0003, seed=0):
    rng = np.random.default_rng(seed)
    close = 1.0 + np.cumsum(rng.normal(drift, 0.001, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    o = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, close) + 0.0005,
                         "low": np.minimum(o, close) - 0.0005, "close": close}, index=idx)


def test_signals_are_only_buy_sell_hold():
    s = MACrossRSITrend(StrategyParams()).signals(trend_df())
    assert set(s.unique()) <= {"BUY", "SELL", "HOLD"}


def test_warmup_produces_no_signal():
    p = StrategyParams()
    s = MACrossRSITrend(p).signals(trend_df())
    assert (s.iloc[:200] == "HOLD").all()


def test_buy_and_sell_both_occur_on_mixed_market():
    found = set()
    for seed in range(5):
        s = MACrossRSITrend(StrategyParams()).signals(trend_df(2000, drift=0.0, seed=seed))
        found |= set(s.unique())
    assert {"BUY", "SELL"} <= found


def test_fast_must_be_below_slow():
    try:
        MACrossRSITrend(StrategyParams(fast_ma=30, slow_ma=20))
        assert False, "aurait dû lever une erreur"
    except ValueError:
        pass
