"""Bot live MT5 — compte DEMO d'abord. Windows uniquement (module MetaTrader5).

Principes :
  - une décision par bougie FERMÉE, une seule fois (idempotence via SQLite) ;
  - le RiskEngine a le dernier mot : entrées, taille, fermeture forcée ;
  - positions MT5 = source de vérité au démarrage ; état persistant dans state/bot_state.db ;
  - arrêt d'urgence : créer le fichier STOP dans le dossier du bot (ferme tout et quitte).

Usage :
  set MT5_LOGIN=12345678
  set MT5_PASSWORD=xxxx
  set MT5_SERVER=BlueGuardian-Demo
  python run_bot.py
"""
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config_loader import ROOT, load_rules, load_settings, load_symbols
from core.execution import MT5Broker
from core.filters import in_news_blackout, in_session, load_news_calendar, spread_ok
from core.risk_engine import RiskEngine
from core.state_store import StateStore
from core.strategy import build_strategy
from monitoring.alerts import notify
from monitoring.logger import get_logger, jlog

MAGIC = 42
STOP_FILE = ROOT / "STOP"


def main() -> int:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("Module MetaTrader5 absent : ce script ne tourne que sous Windows avec MT5 installé.")
        return 1

    log = get_logger("bot")
    settings, rules = load_settings(), load_rules()
    rcfg = settings["risk"]
    tf = getattr(mt5, f"TIMEFRAME_{settings['timeframe']}")
    strat = build_strategy(settings["strategy"])
    news_path = ROOT / "data" / "news.csv"
    news = load_news_calendar(str(news_path)) if news_path.exists() else None
    if news is None:
        jlog(log, "warning", "no_news_calendar", msg="data/news.csv absent : filtre news désactivé")

    (ROOT / "state").mkdir(exist_ok=True)
    store = StateStore(str(ROOT / "state" / "bot_state.db"))
    risk = RiskEngine(rules, rcfg, store)
    broker = MT5Broker(mt5, MAGIC, log)

    if not broker.connect(int(os.getenv("MT5_LOGIN", "0")), os.getenv("MT5_PASSWORD", ""),
                          os.getenv("MT5_SERVER", "")):
        jlog(log, "critical", "connection_failed")
        return 2
    info = mt5.account_info()
    jlog(log, "info", "started", server=info.server, login=info.login, balance=info.balance,
         equity=info.equity, symbols=settings["symbols"])
    notify(f"Bot démarré sur {info.server} (solde {info.balance:.2f})")

    specs = {}
    for sym in settings["symbols"]:
        mt5.symbol_select(sym, True)
        specs[sym] = broker.spec(sym)
        if specs[sym] is not None:
            # sessions et coûts viennent de symbols.yaml (MT5 ne les fournit pas)
            cfg = load_symbols().get(sym, {})
            specs[sym].session_start = cfg.get("session_start")
            specs[sym].session_end = cfg.get("session_end")
        if specs[sym] is None:
            jlog(log, "error", "symbol_unavailable", symbol=sym)

    def open_risk_amount(p) -> float:
        """Risque monétaire actuel d'une position (0 si pas de SL)."""
        sp = specs.get(p.symbol)
        if sp is None or not p.sl:
            return 0.0
        return abs(p.price_open - p.sl) / sp.tick_size * sp.tick_value * p.volume

    def process_symbol(sym: str, now: datetime, st) -> None:
        spec = specs[sym]
        bars = broker.closed_bars(sym, tf, 500)
        if bars is None or len(bars) < 250:
            return
        bar_time = str(bars.index[-1])
        key = f"last_bar:{sym}"
        if store.get(key) == bar_time:
            return  # déjà traitée
        store.set(key, bar_time)

        ohlc = bars[["open", "high", "low", "close"]].astype(float)
        ind = strat.indicators(ohlc)
        side = strat.signals(ohlc).iloc[-1]
        if side not in ("BUY", "SELL"):
            return
        jlog(log, "info", "signal", symbol=sym, side=side, bar=bar_time)

        if not st.allowed_new_trades:
            jlog(log, "info", "entry_blocked", symbol=sym, reason=st.reason)
            return
        s_start = spec.session_start if spec.session_start is not None else settings["session_utc"]["start_hour"]
        s_end = spec.session_end if spec.session_end is not None else settings["session_utc"]["end_hour"]
        if not in_session(now, s_start, s_end):
            return
        if news is not None and in_news_blackout(now.replace(tzinfo=None), news, rules["news_blackout_minutes"]):
            jlog(log, "info", "news_blackout", symbol=sym)
            return
        tick = broker.tick(sym)
        if tick is None:
            return
        spread = tick.ask - tick.bid
        avg_spread = float(bars["spread"].tail(96).mean()) * spec.tick_size
        if not spread_ok(spread, avg_spread, rcfg["max_spread_multiple"]):
            jlog(log, "info", "spread_too_wide", symbol=sym, spread=spread, avg=avg_spread)
            return

        entry = tick.ask if side == "BUY" else tick.bid
        atr_v = float(ind["atr"].iloc[-1])
        sl, tp = risk.stop_levels(side, entry, atr_v, strat.p.sl_atr_mult, strat.p.tp_rr)
        balance, equity = broker.account()
        lots, why = risk.compute_lots(balance, equity, entry, sl, spec, rcfg["risk_per_trade_pct"],
                                      st.size_factor)
        if lots <= 0:
            jlog(log, "info", "lot_rejected", symbol=sym, reason=why)
            return
        risk_amt = lots * abs(entry - sl) / spec.tick_size * spec.tick_value
        mine = broker.bot_positions()
        if mine is None:
            return  # état MT5 inconnu : on n'ouvre rien
        if any(p.symbol == sym for p in mine):
            return  # une seule position par symbole
        open_risk = sum(open_risk_amount(p) for p in mine)
        ok, why = risk.can_open(len(mine), open_risk, risk_amt, balance)
        if not ok:
            jlog(log, "info", "exposure_rejected", symbol=sym, reason=why)
            return
        sent, info_txt = broker.send_market(sym, side, lots, sl, tp, comment="BG_BOT")
        if sent:
            jlog(log, "info", "order_opened", symbol=sym, side=side, lots=lots, sl=sl, tp=tp, detail=info_txt)
            notify(f"Ordre {side} {lots} {sym} SL={sl:.5f} TP={tp:.5f}")
        else:
            jlog(log, "error", "order_failed", symbol=sym, side=side, detail=info_txt)
            notify(f"ERREUR ordre {sym} : {info_txt}")

    while True:
        if STOP_FILE.exists():
            n = broker.close_all()
            jlog(log, "critical", "kill_switch", closed=n)
            notify("ARRÊT D'URGENCE : fichier STOP détecté, positions fermées")
            return 0

        if not broker.ensure_connected():
            jlog(log, "error", "disconnected")
            broker.connect(int(os.getenv("MT5_LOGIN", "0")), os.getenv("MT5_PASSWORD", ""),
                           os.getenv("MT5_SERVER", ""), retries=1)
            time.sleep(settings["check_interval_seconds"])
            continue

        now = datetime.now(timezone.utc)
        acc = broker.account()
        if acc is None:
            time.sleep(settings["check_interval_seconds"])
            continue
        balance, equity = acc
        st = risk.update(now, balance, equity)

        if st.force_close:
            n = broker.close_all()
            jlog(log, "critical", "force_close", reason=st.reason, closed=n, day=st.day)
            notify(f"FERMETURE FORCÉE ({st.reason}) : {n} position(s)")
        else:
            for sym in settings["symbols"]:
                if specs.get(sym) is None:
                    continue
                try:
                    process_symbol(sym, now, st)
                except Exception as e:  # une erreur sur un symbole ne doit pas arrêter le bot
                    jlog(log, "error", "symbol_error", symbol=sym, error=repr(e))

        time.sleep(settings["check_interval_seconds"])


if __name__ == "__main__":
    sys.exit(main())
