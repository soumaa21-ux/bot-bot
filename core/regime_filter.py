"""Filtre de régime : ne trader la tendance que si l'ADX confirme une tendance."""
import pandas as pd


def is_trending(adx_series: pd.Series, adx_min: float) -> pd.Series:
    return adx_series >= adx_min
