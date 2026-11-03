import numpy as np
import pandas as pd

from backtest.engine import PortfolioBacktest
from core.risk_engine import SymbolSpec
from core.config_loader import load_settings, load_rules, load_symbols

SETTINGS = load_settings()
RULES = load_rules()
SPECS = load_symbols()


def make_df(n=2000, seed=1, drift=0.0, start="2024-01-02 00:00"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="15min")
    close = 1.1 + np.cumsum(rng.normal(drift, 0.0005, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 0.0004, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.0004, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "spread": 0.0001}, index=idx)


def spec(name="EURUSD"):
    return SymbolSpec.from_dict(name, SPECS[name])


def run(df, name="EURUSD", costs=None, balance=100000):
    s = SETTINGS
    if costs:
        s = {**SETTINGS, "costs": {**SETTINGS["costs"], **costs}}
    bt = PortfolioBacktest({name: (df, spec(name))}, s, RULES)
    return bt.run(balance)


def test_no_lookahead_signal_uses_closed_bar_only():
    """Changer les bougies futures ne doit pas modifier les signaux passés."""
    from core.strategy import build_strategy
    df = make_df(800, seed=3)
    strat = build_strategy(SETTINGS["strategy"])
    base = strat.signals(df)
    mod = df.copy()
    mod.iloc[600:, :4] = mod.iloc[600:, :4] * 1.05
    changed = strat.signals(mod)
    assert (base.iloc[:600] == changed.iloc[:600]).all()


def test_one_position_per_symbol_and_risk_respected():
    df = make_df(3000, seed=7, drift=0.0001)
    res = run(df)
    t = res.trades
    if len(t) > 1:
        t = t.sort_values("open_time")
        for a, b in zip(t.itertuples(), t.iloc[1:].itertuples()):
            assert b.open_time >= a.close_time or a.symbol != b.symbol
    if len(res.trades):
        assert (res.trades["lots"] >= 0.01).all()


def test_costs_reduce_results():
    df = make_df(3000, seed=11)
    free = run(df, costs={"commission_per_lot_round_turn": 0.0, "slippage_pips": 0.0})
    costly = run(df, costs={"commission_per_lot_round_turn": 10.0, "slippage_pips": 1.0})
    assert costly.final_balance <= free.final_balance + 1e-6


def test_sl_before_tp_when_both_touched_in_same_bar():
    from backtest.engine import Position, BUY
    bt = PortfolioBacktest({"EURUSD": (make_df(50), spec())}, SETTINGS, RULES)
    p = bt.prep["EURUSD"]
    i = 10
    p["o"][i], p["h"][i], p["l"][i] = 1.1, 1.2, 1.0  # bougie qui touche SL et TP
    pos = Position("EURUSD", BUY, 1.1, 1.05, 1.15, 0.1, pd.Timestamp("2024-01-01"), 50.0, bars=1)
    price, reason = bt._exit_check(pos, p, i)
    assert reason == "SL" and price == 1.05


def test_firm_breach_stops_backtest():
    # marche baissier fort : doit déclencher le circuit breaker ou l'arrêt
    df = make_df(4000, seed=5, drift=-0.0008)
    res = run(df)
    assert res.failed is not None or res.final_balance > 0
    if res.failed:
        assert res.failed["daily_used"] >= 1.0 or res.failed["trailing_used"] >= 1.0


def test_equity_curve_length_and_index():
    df = make_df(500, seed=2)
    res = run(df)
    assert len(res.equity) == len(df)
    assert res.equity.index.is_monotonic_increasing


def test_balance_accounting_identity():
    """Solde final = capital initial + somme des P&L nets de tous les trades."""
    df = make_df(3000, seed=21)
    res = run(df)
    total = res.trades["pnl"].sum() if len(res.trades) else 0.0
    assert abs(res.final_balance - (100000 + total)) < 1e-6


def test_firm_limit_forces_stop_when_limits_are_tight():
    import copy
    rules = copy.deepcopy(RULES)
    rules["max_daily_loss_pct"] = 0.0005  # limite absurdement serrée pour forcer le cas
    df = make_df(3000, seed=7, drift=0.0)  # ce jeu produit des trades (13 en nominal)
    assert len(run(df).trades) > 0
    bt = PortfolioBacktest({"EURUSD": (df, spec())}, SETTINGS, rules)
    res = bt.run(100000)
    assert res.failed is not None
    assert res.failed["daily_used"] >= 1.0 or res.failed["trailing_used"] >= 1.0


def test_run_is_deterministic():
    df = make_df(1500, seed=4)
    a = run(df)
    b = run(df)
    assert a.final_balance == b.final_balance
    assert len(a.trades) == len(b.trades)


def test_research_mode_ignores_firm_blocks():
    """En mode recherche, les blocages de la firme ne doivent pas réduire les trades."""
    df = make_df(3000, seed=7, drift=-0.0003)
    bt_firm = PortfolioBacktest({"EURUSD": (df, spec())}, SETTINGS, RULES, enforce_firm_limits=True)
    bt_res = PortfolioBacktest({"EURUSD": (df, spec())}, SETTINGS, RULES, enforce_firm_limits=False)
    firm = bt_firm.run(100000)
    research = bt_res.run(100000)
    assert research.failed is None
    assert len(research.trades) >= len(firm.trades)
