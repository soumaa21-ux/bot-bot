"""Stratégie : croisement de moyennes mobiles, filtré par tendance de fond, RSI et régime (ADX).

Interface commune : `signals(df)` renvoie une Series 'BUY' / 'SELL' / 'HOLD' indexée comme df.
Chaque valeur n'utilise que les bougies jusqu'à sa propre clôture. Le signal est donc
décidé à la clôture de la bougie i et exécuté à l'ouverture de la bougie i+1.
"""
from dataclasses import dataclass

import pandas as pd

from core.indicators import adx, atr, ema, rsi
from core.regime_filter import is_trending


@dataclass
class StrategyParams:
    fast_ma: int = 9
    slow_ma: int = 21
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0
    adx_min: float = 20
    trend_ema: int = 200
    rsi_period: int = 14
    rsi_buy_min: float = 50
    rsi_buy_max: float = 70
    rsi_sell_min: float = 30
    rsi_sell_max: float = 50
    atr_period: int = 14
    adx_period: int = 14
    max_bars_hold: int = 96

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        names = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in names})


class MACrossRSITrend:
    name = "ma_cross_rsi_trend"

    def __init__(self, params: StrategyParams):
        if params.fast_ma >= params.slow_ma:
            raise ValueError("fast_ma doit être < slow_ma")
        self.p = params

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        out = pd.DataFrame(index=df.index)
        out["ma_fast"] = ema(df["close"], p.fast_ma)
        out["ma_slow"] = ema(df["close"], p.slow_ma)
        out["ma_trend"] = ema(df["close"], p.trend_ema)
        out["rsi"] = rsi(df["close"], p.rsi_period)
        out["atr"] = atr(df, p.atr_period)
        out["adx"] = adx(df, p.adx_period)
        return out

    def signals(self, df: pd.DataFrame) -> pd.Series:
        p = self.p
        ind = self.indicators(df)
        prev_fast, prev_slow = ind["ma_fast"].shift(1), ind["ma_slow"].shift(1)
        bull_cross = (prev_fast <= prev_slow) & (ind["ma_fast"] > ind["ma_slow"])
        bear_cross = (prev_fast >= prev_slow) & (ind["ma_fast"] < ind["ma_slow"])
        trending = is_trending(ind["adx"], p.adx_min)

        buy = (bull_cross & trending & (df["close"] > ind["ma_trend"])
               & ind["rsi"].between(p.rsi_buy_min, p.rsi_buy_max))
        sell = (bear_cross & trending & (df["close"] < ind["ma_trend"])
                & ind["rsi"].between(p.rsi_sell_min, p.rsi_sell_max))

        sig = pd.Series("HOLD", index=df.index)
        sig[buy.fillna(False)] = "BUY"
        sig[sell.fillna(False)] = "SELL"
        # Les premières bougies n'ont pas assez d'historique : pas de signal.
        warmup = max(p.trend_ema, p.slow_ma, p.adx_period * 3) + 2
        sig.iloc[:warmup] = "HOLD"
        return sig


def build_strategy(cfg: dict) -> MACrossRSITrend:
    name = cfg.get("name", MACrossRSITrend.name)
    if name != MACrossRSITrend.name:
        raise ValueError(f"Stratégie inconnue : {name}")
    return MACrossRSITrend(StrategyParams.from_dict(cfg))
