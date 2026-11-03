"""Adaptateur MT5 testé avec un faux module : aucun ordre réel."""
import logging
from types import SimpleNamespace as NS

from core.execution import MT5Broker

log = logging.getLogger("test")


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    POSITION_TYPE_BUY = 0

    def __init__(self, retcode=10009, positions=None, none_positions=False):
        self.retcode = retcode
        self.positions = positions if positions is not None else []
        self.none_positions = none_positions
        self.sent = []

    def last_error(self):
        return (-1, "fake")

    def symbol_info(self, s):
        return NS(filling_mode=2, digits=5, point=0.00001, trade_tick_size=0.00001, trade_tick_value=1.0,
                  trade_contract_size=100000, volume_min=0.01, volume_step=0.01, volume_max=100)

    def symbol_info_tick(self, s):
        return NS(bid=1.1, ask=1.1001)

    def order_check(self, req):
        return NS(retcode=0, comment="ok")

    def order_send(self, req):
        self.sent.append(req)
        return NS(retcode=self.retcode, comment="fake")

    def positions_get(self, **kw):
        if self.none_positions:
            return None
        return self.positions


def test_order_refused_is_reported_not_silent():
    b = MT5Broker(FakeMT5(retcode=10018), magic=42, log=log)
    ok, detail = b.send_market("EURUSD", "BUY", 0.1, 1.09, 1.12, "t")
    assert not ok and "10018" in detail


def test_position_not_confirmed_is_flagged():
    b = MT5Broker(FakeMT5(retcode=10009, positions=[]), magic=42, log=log)
    ok, detail = b.send_market("EURUSD", "BUY", 0.1, 1.09, 1.12, "t")
    assert not ok and detail == "position_not_confirmed"


def test_positions_none_handled():
    b = MT5Broker(FakeMT5(none_positions=True), magic=42, log=log)
    assert b.bot_positions() is None


def test_filling_mode_detected_from_symbol():
    b = MT5Broker(FakeMT5(), magic=42, log=log)
    assert b.filling_mode("EURUSD") == FakeMT5.ORDER_FILLING_IOC


class FakeMT5NoSL(FakeMT5):
    """Ordre accepté mais la position revient sans SL : doit être fermée."""
    def __init__(self):
        super().__init__(retcode=10009, positions=[])
        self.closed = []

    def positions_get(self, **kw):
        return [NS(symbol="EURUSD", sl=0.0, ticket=77, magic=42, type=0, volume=0.1, price_open=1.1)]

    def order_send(self, req):
        if "position" in req:
            self.closed.append(req["position"])
            return NS(retcode=10009, comment="")
        return super().order_send(req)


def test_missing_sl_position_is_closed_immediately():
    fake = FakeMT5NoSL()
    b = MT5Broker(fake, magic=42, log=log)
    ok, detail = b.send_market("EURUSD", "BUY", 0.1, 1.09, 1.12, "t")
    assert not ok and detail == "sl_not_confirmed_position_closed"
    assert fake.closed == [77]
