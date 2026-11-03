from datetime import datetime, timezone

import pytest

from core.risk_engine import RiskEngine, SymbolSpec
from core.state_store import StateStore

RULES = {
    "initial_balance": 100000, "max_daily_loss_pct": 0.03, "max_trailing_dd_pct": 0.06,
    "daily_reset": {"timezone": "America/New_York", "hour": 17, "minute": 0},
    "max_leverage": 30, "min_hold_seconds": 120,
}
RISK = {
    "risk_per_trade_pct": 0.005, "max_open_positions": 2, "max_total_open_risk_pct": 0.01,
    "size_factor_warn": 0.5, "daily_warn_level": 0.5, "daily_block_level": 0.7,
    "daily_force_close_level": 0.85, "trailing_block_level": 0.7, "trailing_force_close_level": 0.85,
}
EURUSD = SymbolSpec.from_dict("EURUSD", {
    "digits": 5, "pip_size": 0.0001, "tick_size": 0.00001, "tick_value": 1.0,
    "contract_size": 100000, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100,
    "typical_spread_pips": 0.8})
US500 = SymbolSpec.from_dict("US500", {
    "digits": 2, "pip_size": 1.0, "tick_size": 0.01, "tick_value": 0.5, "contract_size": 50,
    "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50, "typical_spread_pips": 0.6})


def engine(store=None):
    return RiskEngine(RULES, RISK, store or StateStore())


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ---------------------------------------------------------------- sizing
def test_eurusd_lot_sizing_risks_at_most_target():
    re = engine()
    # 0,5 % de 100 000 = 500 $ ; stop de 30 pips = 0.0030 -> 300 $ par lot
    lots, reason = re.compute_lots(100000, 100000, 1.1000, 1.0970, EURUSD, 0.005)
    assert reason == "ok"
    assert lots == pytest.approx(1.66)           # arrondi vers le bas
    assert lots * 300 <= 500 + 1e-6


def test_lot_rounds_down_never_up():
    re = engine()
    lots, _ = re.compute_lots(100000, 100000, 1.1000, 1.0970, EURUSD, 0.005)
    assert lots * 300 <= 500


def test_index_uses_its_own_tick_value():
    re = engine()
    # stop 50 points sur US500 : 50 / 0.01 * 0.5 = 2500 $ par lot ; risque 500 $ -> 0.2 lot
    lots, reason = re.compute_lots(100000, 100000, 5000.0, 4950.0, US500, 0.005)
    assert reason == "ok"
    assert lots == pytest.approx(0.2)


def test_trade_refused_when_min_lot_exceeds_risk():
    re = engine()
    # stop de 6000 pips : 60 000 $ par lot ; 500 $ de risque -> 0,008 lot < 0,01 -> refus
    lots, reason = re.compute_lots(100000, 100000, 1.1000, 0.5000, EURUSD, 0.005)
    assert lots == 0 and reason == "below_min_lot"


def test_size_factor_halves_risk():
    re = engine()
    full, _ = re.compute_lots(100000, 100000, 1.1000, 1.0970, EURUSD, 0.005, 1.0)
    half, _ = re.compute_lots(100000, 100000, 1.1000, 1.0970, EURUSD, 0.005, 0.5)
    assert half < full and half == pytest.approx(0.83)


def test_leverage_cap_applies():
    re = engine()
    # stop très serré -> lots énormes ; plafonné par levier 1:30 sur équité 100 000
    lots, _ = re.compute_lots(100000, 100000, 1.1000, 1.0999, EURUSD, 0.005)
    assert lots * 100000 * 1.1 <= 100000 * 30 + 1e-6


def test_zero_stop_refused():
    re = engine()
    assert re.compute_lots(100000, 100000, 1.1, 1.1, EURUSD, 0.005) == (0.0, "zero_stop")


# ---------------------------------------------------------- drawdown
def test_daily_levels_cascade():
    re = engine()
    t = utc(2026, 11, 3, 12)  # journée de trading démarrée
    s = re.update(t, 100000, 100000)
    assert s.allowed_new_trades and s.size_factor == 1.0
    # perte de 1,6 % (sur limite 3 000 $ : 1 600 / 3 000 = 53 %) -> alerte, taille réduite
    s = re.update(t, 98400, 98400)
    assert s.allowed_new_trades and s.size_factor == 0.5
    # perte de 2,2 % (2 200 / 3 000 = 73 %) -> blocage des entrées
    s = re.update(t, 97800, 97800)
    assert not s.allowed_new_trades and s.reason == "daily_block_level" and not s.force_close
    # perte de 2,6 % (2 600 / 3 000 = 87 %) -> fermeture forcée et pause
    s = re.update(t, 97400, 97400)
    assert s.force_close and not s.allowed_new_trades and s.reason == "daily_halt"


def test_daily_halt_latches_until_reset():
    re = engine()
    t = utc(2026, 11, 3, 12)
    re.update(t, 100000, 100000)
    re.update(t, 97400, 97400)  # halt
    # l'équité remonte un peu : toujours bloqué jusqu'au reset
    s = re.update(utc(2026, 11, 3, 14), 97600, 97600)
    assert s.force_close and s.reason == "daily_halt"


def test_daily_reset_at_17h_new_york():
    re = engine()
    # 16:59 NY (UTC-5 en novembre -> 21:59 UTC) et 17:01 NY (22:01 UTC) : jours différents
    assert re.trading_day(utc(2026, 11, 3, 21, 59)) != re.trading_day(utc(2026, 11, 3, 22, 1))


def test_daily_reset_clears_halt_and_resets_baseline():
    re = engine()
    re.update(utc(2026, 11, 3, 15), 100000, 100000)
    re.update(utc(2026, 11, 3, 16), 97400, 97400)  # halt dans la journée
    s = re.update(utc(2026, 11, 3, 23), 97400, 97400)  # après 17h NY : nouveau jour
    assert not s.force_close and s.allowed_new_trades
    assert s.day_ref == 97400


def test_baseline_survives_restart(tmp_path):
    db = str(tmp_path / "state.db")
    st = StateStore(db)
    re = RiskEngine(RULES, RISK, st)
    re.update(utc(2026, 11, 3, 15), 100000, 100000)
    re.update(utc(2026, 11, 3, 16), 97400, 97400)  # halt
    st.close()
    # redémarrage du bot dans la même journée : le halt doit rester actif
    re2 = RiskEngine(RULES, RISK, StateStore(db))
    s = re2.update(utc(2026, 11, 3, 18), 97400, 97400)
    assert s.force_close and s.day_ref == 100000


def test_trailing_drawdown_locks_and_needs_manual_unlock():
    re = engine()
    t = utc(2026, 11, 3, 12)
    # hausse à 104 000 (HWM)
    re.update(t, 104000, 104000)
    # equity 98 900 : perte de 5 100 depuis le HWM ; limite 6 000 -> 85 % -> verrou
    s = re.update(utc(2026, 11, 4, 12), 98900, 98900)
    assert s.force_close and s.reason == "trailing_locked"
    # même si l'équité remonte, le verrou reste
    s = re.update(utc(2026, 11, 5, 12), 103000, 103000)
    assert not s.allowed_new_trades
    re.unlock_trailing(utc(2026, 11, 5, 13))
    s = re.update(utc(2026, 11, 5, 14), 103000, 103000)
    assert s.allowed_new_trades


def test_breach_event_logged():
    st = StateStore()
    re = engine(st)
    re.update(utc(2026, 11, 3, 12), 100000, 100000)
    re.update(utc(2026, 11, 3, 13), 96900, 96900)  # perte de 3 100 > 3 000
    kinds = [e[2] for e in st.events()]
    assert "firm_limit_breached" in kinds


# ---------------------------------------------------------- exposition
def test_exposure_limits():
    re = engine()
    assert re.can_open(0, 0, 500, 100000) == (True, "ok")
    assert re.can_open(2, 0, 500, 100000) == (False, "max_open_positions")
    assert re.can_open(1, 600, 500, 100000) == (False, "max_total_open_risk")


def test_min_hold_for_manual_close():
    re = engine()
    t0 = utc(2026, 11, 3, 12)
    assert not re.manual_close_allowed(t0, utc(2026, 11, 3, 12, 1))
    assert re.manual_close_allowed(t0, utc(2026, 11, 3, 12, 3))
