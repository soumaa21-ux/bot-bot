"""Moteur de backtest portefeuille, réaliste.

- Données : CSV de prix BID. Achat à l'ASK (bid + spread), vente au BID.
- Signal à la clôture de la bougie i-1, exécution à l'ouverture de la bougie i (+ slippage défavorable).
- Commission par lot aller-retour, slippage, spread variable du CSV ou spread par défaut.
- Sorties intrabar pessimistes : si SL et TP sont touchés dans la même bougie, le SL gagne.
- Équité flottante modélisée avec le pire prix intrabar, pour le drawdown journalier et trailing.
- Même RiskEngine que le live : même sizing, mêmes niveaux de blocage et de fermeture forcée.
- Un solde partagé entre symboles, une seule position par symbole, plafonds d'exposition.
"""
from dataclasses import dataclass, field, replace
from datetime import timezone

import numpy as np
import pandas as pd

from core.filters import in_news_blackout, in_session, spread_ok
from core.risk_engine import RiskEngine, SymbolSpec
from core.state_store import StateStore
from core.strategy import build_strategy

BUY, SELL = "BUY", "SELL"


@dataclass
class Position:
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float
    lots: float
    open_time: pd.Timestamp
    risk_amt: float
    bars: int = 0


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series
    initial_balance: float
    final_balance: float
    failed: dict | None
    risk_events: list = field(default_factory=list)


class PortfolioBacktest:
    def __init__(self, data: dict, settings: dict, rules: dict, news: pd.DatetimeIndex | None = None,
                 store: StateStore | None = None, strategy_params: dict | None = None,
                 enforce_firm_limits: bool = True):
        """data : {symbole: (DataFrame OHLC bid normalisé, SymbolSpec)}

        enforce_firm_limits=True  : simulation du compte réel (blocages, fermetures, arrêt à la rupture).
        enforce_firm_limits=False : mode RECHERCHE. Mesure l'edge des trades sans que les règles de la firme
                                    gèlent le compte. Les niveaux sont calculés et rapportés, mais ignorés.
        """
        self.enforce = enforce_firm_limits
        self.settings = settings
        self.rules = rules
        self.news = news
        self.news_minutes = rules.get("news_blackout_minutes", 0)
        self.risk_cfg = settings["risk"]
        self.costs = settings["costs"]
        self.sess = settings["session_utc"]
        self.store = store or StateStore()
        self.risk = RiskEngine(rules, self.risk_cfg, self.store)
        self.budgets = dict(self.risk_cfg.get("market_budgets") or {})
        self.budget_close = float(self.risk_cfg["trailing_force_close_level"])
        self.initial = float(rules["initial_balance"])
        cfg = dict(settings["strategy"])
        if strategy_params:
            cfg.update(strategy_params)
        self.strategy_cfg = cfg
        self.prep = {}
        for name, (df, spec) in data.items():
            self.prep[name] = self._prepare(name, df, spec)

    # ----------------------------------------------------------- préparation
    def _prepare(self, name: str, df: pd.DataFrame, spec: SymbolSpec) -> dict:
        strat = build_strategy(self.strategy_cfg)
        ind = strat.indicators(df)
        sig = strat.signals(df)
        if "spread" in df.columns:
            spread = df["spread"].astype(float)
        else:
            typ = spec.typical_spread_pips or self.costs["default_spread_pips"]
            spread = pd.Series(typ * spec.pip_size, index=df.index)
        avg = spread.rolling(96, min_periods=1).mean().shift(1).fillna(spread)
        return dict(
            spec=spec, strat=strat, max_hold=strat.p.max_bars_hold,
            sl_mult=strat.p.sl_atr_mult, tp_rr=strat.p.tp_rr,
            idx={ts: i for i, ts in enumerate(df.index)},
            o=df["open"].values, h=df["high"].values, l=df["low"].values, c=df["close"].values,
            sig=sig.values, atr=ind["atr"].values, sp=spread.values, avg_sp=avg.values,
            slip=self.costs["slippage_pips"] * spec.pip_size,
            comm=float(spec.commission_per_lot if spec.commission_per_lot is not None
                       else self.costs["commission_per_lot_round_turn"]),
            sess_start=spec.session_start if spec.session_start is not None else self.sess["start_hour"],
            sess_end=spec.session_end if spec.session_end is not None else self.sess["end_hour"],
            ts_idx=df.index,
        )

    # ------------------------------------------------------------ maths
    @staticmethod
    def _pnl(pos: Position, price: float, spec: SymbolSpec) -> float:
        diff = (price - pos.entry) if pos.side == BUY else (pos.entry - price)
        return diff / spec.tick_size * spec.tick_value * pos.lots

    # ------------------------------------------------------------- run
    def run(self, initial_balance: float = 100000.0) -> BacktestResult:
        names = sorted(self.prep)
        balance = float(initial_balance)
        positions: dict[str, Position | None] = {n: None for n in names}
        last_mark = {n: None for n in names}
        trades: list[dict] = []
        eq_times, eq_vals = [], []
        failed = None
        risk = self.risk
        risk_cfg = self.risk_cfg
        pct = risk_cfg["risk_per_trade_pct"]
        max_mult = risk_cfg["max_spread_multiple"]

        all_times = sorted(set().union(*[set(self.prep[n]["ts_idx"]) for n in names]))

        sym_cum = {n: 0.0 for n in names}      # PnL net réalisé par marché
        sym_hwm = {n: 0.0 for n in names}      # plus haut du PnL par marché
        sym_locked = {n: False for n in names}  # marché gelé par son budget

        def close(n: str, price: float, reason: str, t) -> None:
            nonlocal balance
            pos = positions[n]
            spec = self.prep[n]["spec"]
            gross = self._pnl(pos, price, spec)
            commission = self.prep[n]["comm"] * pos.lots
            net = gross - commission
            balance += net
            sym_cum[n] += net
            trades.append(dict(symbol=n, side=pos.side, open_time=pos.open_time, close_time=t,
                               entry=pos.entry, exit=price, sl=pos.sl, tp=pos.tp, lots=pos.lots,
                               pnl=net, R=net / pos.risk_amt if pos.risk_amt else 0.0,
                               reason=reason, bars=pos.bars))
            positions[n] = None

        for t in all_times:
            present = [n for n in names if t in self.prep[n]["idx"]]

            # 1) équité au pire prix intrabar -> état du risque
            worst = 0.0
            sym_worst = {}
            for n in names:
                pos = positions[n]
                if pos is None:
                    continue
                p = self.prep[n]
                if n in present:
                    i = p["idx"][t]
                    price = p["l"][i] if pos.side == BUY else p["h"][i] + p["sp"][i]
                else:
                    price = last_mark[n]
                v = self._pnl(pos, price, p["spec"])
                worst += v
                sym_worst[n] = v

            # Budget par marché : fermeture et gel de ce seul marché
            if self.enforce and self.budgets:
                for n in names:
                    if n not in self.budgets or sym_locked[n]:
                        continue
                    eq_n = sym_cum[n] + sym_worst.get(n, 0.0)
                    sym_hwm[n] = max(sym_hwm[n], sym_cum[n])
                    used = (sym_hwm[n] - eq_n) / (self.initial * float(self.budgets[n]))
                    if used >= self.budget_close:
                        sym_locked[n] = True
                        if positions[n] is not None:
                            p = self.prep[n]
                            price = (p["l"][p["idx"][t]] if n in present else last_mark[n]) \
                                if positions[n].side == BUY else \
                                ((p["h"][p["idx"][t]] + p["sp"][p["idx"][t]]) if n in present else last_mark[n])
                            close(n, price, "MARKET_BUDGET", t)
                            worst -= sym_worst.get(n, 0.0)
                            worst += 0.0

            st = risk.update(t.to_pydatetime().replace(tzinfo=timezone.utc), balance, balance + worst)
            if st.daily_used >= 1.0 or st.trailing_used >= 1.0:
                if self.enforce:
                    failed = dict(time=str(t), reason="firm_limit", daily_used=st.daily_used,
                                  trailing_used=st.trailing_used)
                    break
            if not self.enforce:
                st = replace(st, allowed_new_trades=True, force_close=False, size_factor=1.0, reason="research")

            # 2) sorties sur la bougie courante (SL, TP, temps)
            for n in present:
                pos = positions[n]
                if pos is None:
                    continue
                p = self.prep[n]
                i = p["idx"][t]
                pos.bars += 1
                ex = self._exit_check(pos, p, i)
                if ex:
                    close(n, ex[0], ex[1], t)

            # 3) fermeture forcée (circuit breaker niveau 2)
            if st.force_close:
                for n in names:
                    if positions[n] is not None:
                        p = self.prep[n]
                        price = (p["c"][p["idx"][t]] if n in present else last_mark[n])
                        if positions[n].side == SELL and n in present:
                            price = p["c"][p["idx"][t]] + p["sp"][p["idx"][t]]
                        close(n, price, "FORCE_CLOSE", t)

            # 4) entrées
            if st.allowed_new_trades:
                for n in present:
                    if positions[n] is not None or sym_locked[n]:
                        continue
                    p = self.prep[n]
                    i = p["idx"][t]
                    if i == 0:
                        continue
                    side = p["sig"][i - 1]
                    if side not in (BUY, SELL):
                        continue
                    ts = t.to_pydatetime().replace(tzinfo=timezone.utc)
                    if not in_session(ts, p["sess_start"], p["sess_end"]):
                        continue
                    if self.news is not None and in_news_blackout(t, self.news, self.news_minutes):
                        continue
                    if not spread_ok(p["sp"][i], p["avg_sp"][i], max_mult):
                        continue
                    spec = p["spec"]
                    if side == BUY:
                        entry = p["o"][i] + p["sp"][i] + p["slip"]
                    else:
                        entry = p["o"][i] - p["slip"]
                    atr_prev = p["atr"][i - 1]
                    if not np.isfinite(atr_prev) or atr_prev <= 0:
                        continue
                    sl, tp = risk.stop_levels(side, entry, atr_prev, p["sl_mult"], p["tp_rr"])
                    lots, _ = risk.compute_lots(balance, balance, entry, sl, spec, pct, st.size_factor)
                    if lots <= 0:
                        continue
                    risk_amt = lots * abs(entry - sl) / spec.tick_size * spec.tick_value
                    open_pos = [q for q in positions.values() if q is not None]
                    open_risk = sum(q.risk_amt for q in open_pos)
                    ok, _ = risk.can_open(len(open_pos), open_risk, risk_amt, balance)
                    if not ok:
                        continue
                    positions[n] = Position(n, side, entry, sl, tp, lots, t, risk_amt)

            # 5) sorties sur la bougie d'entrée (pessimiste : même bougie)
            for n in present:
                pos = positions[n]
                if pos is not None and pos.bars == 0:
                    p = self.prep[n]
                    ex = self._exit_check(pos, p, p["idx"][t])
                    if ex:
                        close(n, ex[0], ex[1], t)

            # 6) marquage à la clôture
            mark = balance
            for n in names:
                pos = positions[n]
                p = self.prep[n]
                if n in present:
                    i = p["idx"][t]
                    price = p["c"][i] if pos is None or pos.side == BUY else p["c"][i] + p["sp"][i]
                    last_mark[n] = price
                elif last_mark[n] is None:
                    continue
                if pos is not None:
                    mark += self._pnl(pos, last_mark[n], p["spec"])
            eq_times.append(t)
            eq_vals.append(mark)

        # clôture des positions restantes au dernier prix (fin de données)
        for n in names:
            if positions[n] is not None:
                close(n, last_mark[n], "END_OF_DATA", all_times[-1])

        trades_df = pd.DataFrame(trades)
        equity = pd.Series(eq_vals, index=pd.DatetimeIndex(eq_times), name="equity")
        return BacktestResult(trades=trades_df, equity=equity, initial_balance=initial_balance,
                              final_balance=balance, failed=failed)

    # ---------------------------------------------------------- sorties
    def _exit_check(self, pos: Position, p: dict, i: int):
        if pos.side == BUY:
            o, lo, hi = p["o"][i], p["l"][i], p["h"][i]
            if o <= pos.sl:
                return o, "SL"
            if lo <= pos.sl:
                return pos.sl, "SL"
            if o >= pos.tp:
                return o, "TP"
            if hi >= pos.tp:
                return pos.tp, "TP"
        else:
            sp = p["sp"][i]
            o, lo, hi = p["o"][i] + sp, p["l"][i] + sp, p["h"][i] + sp
            if o >= pos.sl:
                return o, "SL"
            if hi >= pos.sl:
                return pos.sl, "SL"
            if o <= pos.tp:
                return o, "TP"
            if lo <= pos.tp:
                return pos.tp, "TP"
        if pos.bars >= p["max_hold"]:
            price = p["c"][i] if pos.side == BUY else p["c"][i] + p["sp"][i]
            return price, "TIME"
        return None
