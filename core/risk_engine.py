"""Moteur de risque : seule autorité sur les entrées, la taille et les fermetures forcées.

Indépendant de la stratégie. Utilisé à l'identique par le backtest et le live
(le temps est passé en paramètre, jamais lu depuis l'horloge machine).

Règles :
  - drawdown journalier mesuré depuis la baseline de début de journée (reset à l'heure du firm),
    la baseline est persistée pour survivre à un redémarrage ;
  - drawdown total trailing depuis le plus haut solde clôturé ;
  - niveaux en cascade : alerte -> réduction de taille -> blocage des entrées -> fermeture forcée ;
  - sizing sur tick_value / tick_size, arrondi vers le bas au volume_step, levier plafonné.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import floor
from zoneinfo import ZoneInfo

from core.state_store import StateStore


@dataclass
class SymbolSpec:
    name: str
    digits: int
    pip_size: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    typical_spread_pips: float
    quote_is_usd: bool = True
    session_start: int | None = None         # heure UTC incluse ; None = session des settings
    session_end: int | None = None
    commission_per_lot: float | None = None  # None = valeur par défaut des settings

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "SymbolSpec":
        return cls(name=name, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "name"})


@dataclass
class RiskStatus:
    allowed_new_trades: bool
    size_factor: float
    force_close: bool
    reason: str
    day: str
    day_ref: float
    hwm: float
    daily_used: float
    trailing_used: float


class RiskEngine:
    def __init__(self, rules: dict, risk_cfg: dict, store: StateStore):
        self.initial = float(rules["initial_balance"])
        self.daily_pct = float(rules["max_daily_loss_pct"])
        self.trailing_pct = float(rules["max_trailing_dd_pct"])
        rs = rules["daily_reset"]
        self.tz = ZoneInfo(rs["timezone"])
        self.reset_hour = int(rs["hour"])
        self.reset_minute = int(rs["minute"])
        self.max_leverage = float(rules["max_leverage"])
        self.rules = rules
        self.min_hold_seconds = float(rules.get("min_hold_seconds", 0))
        self.r = risk_cfg
        self.store = store
        if store.get("hwm") is None:
            store.set("hwm", self.initial)
        if store.get("trailing_locked") is None:
            store.set("trailing_locked", False)

    # ------------------------------------------------------------------ temps
    def trading_day(self, now_utc: datetime) -> str:
        """Jour de trading : change à l'heure de reset du firm (ex. 17:00 New York)."""
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        local = now_utc.astimezone(self.tz)
        shifted = local - timedelta(hours=self.reset_hour, minutes=self.reset_minute)
        return shifted.date().isoformat()

    # ------------------------------------------------------------- état
    def update(self, now_utc: datetime, balance: float, equity: float) -> RiskStatus:
        day = self.trading_day(now_utc)
        if self.store.get("day") != day:
            day_ref = max(balance, equity)
            self.store.set("day", day)
            self.store.set("day_ref", day_ref)
            self.store.set("daily_halt", False)
            self.store.log_event("INFO", "daily_reset", {"day": day, "day_ref": day_ref}, now_utc)

        hwm = self.store.get("hwm")
        if balance > hwm:
            hwm = balance
            self.store.set("hwm", hwm)

        day_ref = self.store.get("day_ref")
        daily_limit = self.initial * self.daily_pct
        trailing_limit = self.initial * self.trailing_pct
        daily_used = max(0.0, day_ref - equity) / daily_limit
        trailing_used = max(0.0, hwm - equity) / trailing_limit

        if daily_used >= 1.0 or trailing_used >= 1.0:
            if not self.store.get("breach_logged"):
                self.store.set("breach_logged", True)
                self.store.log_event("CRITICAL", "firm_limit_breached",
                                     {"daily_used": daily_used, "trailing_used": trailing_used,
                                      "equity": equity}, now_utc)

        if daily_used >= self.r["daily_force_close_level"] and not self.store.get("daily_halt"):
            self.store.set("daily_halt", True)
            self.store.log_event("WARNING", "daily_force_close",
                                 {"daily_used": daily_used, "equity": equity}, now_utc)
        rearm = self.rules.get("trailing_rearm", {}) or {}
        locks = int(self.store.get("trailing_lock_count") or 0)
        if trailing_used >= self.r["trailing_force_close_level"] and not self.store.get("trailing_locked") \
                and not self.store.get("trailing_dead"):
            self.store.set("trailing_locked", True)
            locks += 1
            self.store.set("trailing_lock_count", locks)
            self.store.log_event("CRITICAL", "trailing_locked",
                                 {"trailing_used": trailing_used, "equity": equity, "lock_count": locks}, now_utc)
            if rearm.get("enabled", False) and locks >= int(rearm.get("max_locks", 1)):
                self.store.set("trailing_dead", True)
                self.store.log_event("CRITICAL", "trailing_dead", {"lock_count": locks}, now_utc)

        # Reprise après coupure : seulement si l'équité est revenue près du plus haut
        if (self.store.get("trailing_locked") and not self.store.get("trailing_dead")
                and rearm.get("enabled", False)
                and (hwm - equity) / hwm <= float(rearm["max_dd_pct_to_rearm"])):
            self.store.set("trailing_locked", False)
            self.store.set("reduced_size", True)
            self.store.log_event("WARNING", "trailing_rearmed", {"equity": equity, "hwm": hwm}, now_utc)

        if self.store.get("reduced_size") and (hwm - equity) / hwm <= 0.0:
            self.store.set("reduced_size", False)

        daily_halt = bool(self.store.get("daily_halt"))
        trailing_locked = bool(self.store.get("trailing_locked"))
        dead = bool(self.store.get("trailing_dead"))

        size_factor = 1.0
        if self.store.get("reduced_size") and rearm.get("enabled", False):
            size_factor = float(rearm["size_factor"])
        if max(daily_used, trailing_used) >= self.r["daily_warn_level"]:
            size_factor = self.r["size_factor_warn"]

        reason = "ok"
        allowed = True
        if dead:
            allowed, reason = False, "trailing_dead"
        elif trailing_locked:
            allowed, reason = False, "trailing_locked"
        elif daily_halt:
            allowed, reason = False, "daily_halt"
        elif daily_used >= self.r["daily_block_level"]:
            allowed, reason = False, "daily_block_level"
        elif trailing_used >= self.r["trailing_block_level"]:
            allowed, reason = False, "trailing_block_level"

        force_close = daily_halt or trailing_locked or dead
        return RiskStatus(allowed, size_factor, force_close, reason, day, day_ref, hwm,
                          daily_used, trailing_used)

    def unlock_trailing(self, now_utc: datetime) -> None:
        """Déverrouillage manuel uniquement (après analyse humaine)."""
        self.store.set("trailing_locked", False)
        self.store.log_event("WARNING", "trailing_unlocked_manually", {}, now_utc)

    # ------------------------------------------------------------ sizing
    def compute_lots(self, balance: float, equity: float, entry: float, sl: float,
                     spec: SymbolSpec, risk_pct: float, size_factor: float = 1.0) -> tuple[float, str]:
        """Retourne (lots, raison). lots = 0 si le trade doit être refusé."""
        if not spec.quote_is_usd:
            raise NotImplementedError("Sizing implémenté seulement pour devise de cotation = compte")
        dist = abs(entry - sl)
        if dist <= 0:
            return 0.0, "zero_stop"
        base = min(balance, equity)
        risk_amt = base * risk_pct * size_factor
        loss_per_lot = dist / spec.tick_size * spec.tick_value
        if loss_per_lot <= 0:
            return 0.0, "bad_spec"
        step = spec.volume_step
        lots = floor(risk_amt / loss_per_lot / step + 1e-9) * step
        lots = min(lots, spec.volume_max)

        notional_per_lot = spec.contract_size * entry
        max_notional = equity * self.max_leverage
        lots = min(lots, floor(max_notional / notional_per_lot / step + 1e-9) * step)

        lots = round(lots, 2)
        if lots < spec.volume_min:
            return 0.0, "below_min_lot"
        return lots, "ok"

    def stop_levels(self, side: str, entry: float, atr_value: float, sl_mult: float,
                    tp_rr: float) -> tuple[float, float]:
        d = atr_value * sl_mult
        if side == "BUY":
            return entry - d, entry + d * tp_rr
        return entry + d, entry - d * tp_rr

    # ----------------------------------------------------- exposition
    def can_open(self, open_positions: int, open_risk_amt: float, new_risk_amt: float,
                 balance: float) -> tuple[bool, str]:
        if open_positions >= self.r["max_open_positions"]:
            return False, "max_open_positions"
        if open_risk_amt + new_risk_amt > balance * self.r["max_total_open_risk_pct"] + 1e-9:
            return False, "max_total_open_risk"
        return True, "ok"

    def manual_close_allowed(self, opened_at: datetime, now_utc: datetime) -> bool:
        """Règle de holding minimum (2 min) pour les fermetures discrétionnaires du bot."""
        return (now_utc - opened_at).total_seconds() >= self.min_hold_seconds
