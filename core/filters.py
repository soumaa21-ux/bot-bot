"""Filtres d'entrée : session, news, spread. Tous pris en compte par le backtest et le live."""
from datetime import datetime, timedelta, timezone

import pandas as pd


def in_session(ts: datetime, start_hour: int, end_hour: int) -> bool:
    """Heures UTC incluses (start_hour <= h <= end_hour)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return start_hour <= ts.astimezone(timezone.utc).hour <= end_hour


def in_news_blackout(ts: datetime, events: pd.DatetimeIndex, minutes: int) -> bool:
    """True si ts est à moins de `minutes` d'une annonce à fort impact."""
    if events is None or len(events) == 0:
        return False
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    window = pd.Timedelta(minutes=minutes)
    idx = events.searchsorted(t)
    for k in (idx - 1, idx):
        if 0 <= k < len(events) and abs(events[k] - t) <= window:
            return True
    return False


def load_news_calendar(path: str) -> pd.DatetimeIndex:
    """CSV avec une colonne 'time' (UTC) pour chaque annonce à fort impact."""
    df = pd.read_csv(path)
    times = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return pd.DatetimeIndex(times.sort_values().unique())


def spread_ok(spread: float, avg_spread: float, max_multiple: float) -> bool:
    if avg_spread <= 0:
        return spread >= 0
    return spread <= avg_spread * max_multiple
