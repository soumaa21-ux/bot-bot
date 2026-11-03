"""Adaptateur MetaTrader 5 (Windows uniquement).

Le module `MetaTrader5` est importé à l'exécution : les tests et le backtest n'en ont pas besoin.
Toutes les réponses None sont gérées. Chaque ordre est vérifié (order_check, retcode, SL présent).
"""
import time

from core.risk_engine import SymbolSpec

TRADE_RETCODE_DONE = 10009


class MT5Broker:
    def __init__(self, mt5_module, magic: int, log):
        self.mt5 = mt5_module
        self.magic = magic
        self.log = log

    # ----------------------------------------------------------- connexion
    def connect(self, login: int, password: str, server: str, retries: int = 5) -> bool:
        delay = 2
        for attempt in range(1, retries + 1):
            if self.mt5.initialize(login=login, password=password, server=server):
                return True
            self.log.error("MT5 init échoué (essai %d) : %s", attempt, self.mt5.last_error())
            time.sleep(delay)
            delay = min(delay * 2, 60)
        return False

    def ensure_connected(self) -> bool:
        return self.mt5.account_info() is not None

    # ---------------------------------------------------------- données
    def account(self):
        info = self.mt5.account_info()
        return (info.balance, info.equity) if info else None

    def closed_bars(self, symbol: str, timeframe, n: int = 500):
        """Bougies FERMÉES uniquement (position 1 ; la position 0 est la bougie en cours)."""
        rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 1, n)
        if rates is None or len(rates) == 0:
            return None
        import pandas as pd
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df

    def tick(self, symbol: str):
        return self.mt5.symbol_info_tick(symbol)

    def spec(self, symbol: str) -> SymbolSpec | None:
        si = self.mt5.symbol_info(symbol)
        if si is None:
            return None
        return SymbolSpec(
            name=symbol, digits=si.digits, pip_size=si.point * (10 if si.digits in (3, 5) else 1),
            tick_size=si.trade_tick_size, tick_value=si.trade_tick_value,
            contract_size=si.trade_contract_size, volume_min=si.volume_min,
            volume_step=si.volume_step, volume_max=si.volume_max,
            typical_spread_pips=0.0, quote_is_usd=True)

    def filling_mode(self, symbol: str) -> int:
        si = self.mt5.symbol_info(symbol)
        mode = si.filling_mode if si else 0
        if mode & 1:
            return self.mt5.ORDER_FILLING_FOK
        if mode & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    # ---------------------------------------------------------- positions
    def bot_positions(self):
        pos = self.mt5.positions_get()
        if pos is None:
            self.log.error("positions_get a renvoyé None : %s", self.mt5.last_error())
            return None
        return [p for p in pos if p.magic == self.magic]

    # ------------------------------------------------------------ ordres
    def send_market(self, symbol: str, side: str, lots: float, sl: float, tp: float,
                    comment: str, deviation: int = 20):
        tick = self.tick(symbol)
        if tick is None:
            return False, "no_tick"
        price = tick.ask if side == "BUY" else tick.bid
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
            "type": self.mt5.ORDER_TYPE_BUY if side == "BUY" else self.mt5.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "tp": tp, "deviation": deviation, "magic": self.magic,
            "comment": comment, "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.filling_mode(symbol),
        }
        chk = self.mt5.order_check(req)
        if chk is None or chk.retcode != 0:
            return False, f"order_check:{getattr(chk, 'retcode', None)}:{getattr(chk, 'comment', '')}"
        res = self.mt5.order_send(req)
        if res is None or res.retcode != TRADE_RETCODE_DONE:
            return False, f"order_send:{getattr(res, 'retcode', None)}:{getattr(res, 'comment', '')}"
        # Vérification post-ordre : la position existe ET son SL est bien celui demandé.
        # Sinon, fermeture immédiate (règle de la roadmap : pas de position sans stop).
        time.sleep(0.5)
        si = self.mt5.symbol_info(symbol)
        digits = si.digits if si else 5
        tol = 0.5 * 10 ** (-digits)
        mine = [p for p in (self.bot_positions() or []) if p.symbol == symbol]
        good = [p for p in mine if p.sl and abs(p.sl - sl) <= tol]
        if good:
            return True, f"ticket={good[-1].ticket}"
        if not mine:
            return False, "position_not_confirmed"
        for p in mine:
            self.log.critical("Position sans SL confirmé, fermeture immédiate : ticket %s", p.ticket)
            self.close_position(p)
        return False, "sl_not_confirmed_position_closed"

    def close_position(self, pos) -> bool:
        tick = self.tick(pos.symbol)
        if tick is None:
            return False
        is_buy = pos.type == self.mt5.POSITION_TYPE_BUY
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": 30, "magic": self.magic, "comment": "BG_CLOSE",
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self.filling_mode(pos.symbol),
        }
        res = self.mt5.order_send(req)
        return bool(res and res.retcode == TRADE_RETCODE_DONE)

    def close_all(self) -> int:
        pos = self.bot_positions() or []
        closed = 0
        for p in pos:
            if self.close_position(p):
                closed += 1
        return closed
