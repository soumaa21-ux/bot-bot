from datetime import datetime, timezone

import pandas as pd

from core.filters import in_news_blackout, in_session, spread_ok


def test_session_hours_utc():
    assert in_session(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc), 7, 20)
    assert in_session(datetime(2026, 11, 3, 20, 59, tzinfo=timezone.utc), 7, 20)
    assert not in_session(datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc), 7, 20)


def test_news_blackout_both_sides():
    news = pd.DatetimeIndex(["2026-11-03 13:30"])
    assert in_news_blackout(datetime(2026, 11, 3, 13, 20), news, 15)
    assert in_news_blackout(datetime(2026, 11, 3, 13, 44), news, 15)
    assert not in_news_blackout(datetime(2026, 11, 3, 14, 0), news, 15)


def test_spread_filter():
    assert spread_ok(0.0002, 0.0001, 3.0)
    assert not spread_ok(0.0004, 0.0001, 3.0)
