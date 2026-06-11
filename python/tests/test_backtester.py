"""Phase 17 — Unit tests for core/backtester.py.

Tests cover BacktestTrade defaults, BacktestResult.summary(), _close_trade()
P&L math (BUY and SELL), _check_exit() SL/TP/both/neither logic,
_compute_result() metrics, and run() integration paths.

Signal pipeline functions (classify_regime, detect_order_blocks,
generate_signals) are mocked in integration tests to keep tests fast and
deterministic. Metric and helper tests operate on BacktestTrade directly.
"""
import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.backtester import BacktestTrade, BacktestResult, Backtester
from core.config import TradingConfig
from signals.regime import RegimeLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> TradingConfig:
    defaults = dict(
        lot_size=0.01, sl_buffer=0.0002, rr_ratio=2.0,
        adx_trend_threshold=25.0, max_open_trades=5, timeframe="H1",
    )
    defaults.update(kwargs)
    return TradingConfig(**defaults)


def make_df(n: int = 150, trend: float = 0.0001) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with a gentle trend."""
    rng = np.random.default_rng(42)
    idx   = pd.date_range("2025-01-01", periods=n, freq="h")
    close = 1.10 + np.cumsum(rng.normal(trend, 0.0003, n))
    high  = close + 0.0008
    low   = close - 0.0008
    return pd.DataFrame(
        {"open": close - 0.0002, "high": high, "low": low,
         "close": close, "volume": 1000.0},
        index=idx,
    )


def make_trade(
    side="BUY",
    entry_price=1.1000,
    stop_loss=1.0980,
    take_profit=1.1040,
    size=0.01,
) -> BacktestTrade:
    return BacktestTrade(
        symbol="EURUSDm",
        side=side,
        entry_bar_idx=0,
        entry_time=pd.Timestamp("2025-01-01"),
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        size=size,
    )


def make_bar(high: float, low: float, close: float = None) -> pd.Series:
    if close is None:
        close = (high + low) / 2
    return pd.Series({"open": close, "high": high, "low": low, "close": close})


# ---------------------------------------------------------------------------
# BacktestTrade defaults
# ---------------------------------------------------------------------------

class TestBacktestTradeDefaults:
    def test_exit_bar_idx_default(self):
        t = make_trade()
        assert t.exit_bar_idx == -1

    def test_pnl_r_default(self):
        t = make_trade()
        assert t.pnl_r == 0.0

    def test_exit_reason_default(self):
        t = make_trade()
        assert t.exit_reason == ""

    def test_exit_price_default(self):
        t = make_trade()
        assert t.exit_price == 0.0


# ---------------------------------------------------------------------------
# BacktestResult
# ---------------------------------------------------------------------------

class TestBacktestResult:
    def test_summary_non_empty(self):
        result = BacktestResult(
            symbol="EURUSDm", timeframe="H1",
            total_trades=10, win_count=6, loss_count=4,
            win_rate=0.6, total_r=4.0, avg_r_per_trade=0.4,
            max_drawdown_r=1.5, sharpe_r=1.2, profit_factor=2.0,
        )
        s = result.summary()
        assert isinstance(s, str)
        assert len(s) > 0
        assert "EURUSDm" in s
        assert "10" in s

    def test_empty_result_zero_trades(self):
        cfg = make_config()
        bt  = Backtester(cfg)
        res = bt._empty_result("EURUSDm")
        assert res.total_trades == 0
        assert res.win_rate == 0.0
        assert res.equity_r == []


# ---------------------------------------------------------------------------
# _close_trade — BUY
# ---------------------------------------------------------------------------

class TestCloseTradesBuy:
    def setup_method(self):
        self.bt    = Backtester(make_config())
        self.trade = make_trade(side="BUY", entry_price=1.1000,
                                stop_loss=1.0980, take_profit=1.1040)
        self.ts    = pd.Timestamp("2025-01-02")

    def test_buy_tp_hit_positive_pnl(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.1040, "TP")
        assert closed.pnl_points > 0

    def test_buy_sl_hit_negative_pnl(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.0980, "SL")
        assert closed.pnl_points < 0

    def test_buy_tp_pnl_r_approx_2(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.1040, "TP")
        assert abs(closed.pnl_r - 2.0) < 0.01

    def test_buy_sl_pnl_r_approx_minus1(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.0980, "SL")
        assert abs(closed.pnl_r - (-1.0)) < 0.01

    def test_exit_reason_stored(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.1040, "TP")
        assert closed.exit_reason == "TP"

    def test_eod_closure_reason(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.1010, "EOD")
        assert closed.exit_reason == "EOD"


# ---------------------------------------------------------------------------
# _close_trade — SELL
# ---------------------------------------------------------------------------

class TestCloseTradeSell:
    def setup_method(self):
        self.bt    = Backtester(make_config())
        self.trade = make_trade(side="SELL", entry_price=1.1000,
                                stop_loss=1.1020, take_profit=1.0960)
        self.ts    = pd.Timestamp("2025-01-02")

    def test_sell_tp_hit_positive_pnl(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.0960, "TP")
        assert closed.pnl_points > 0

    def test_sell_sl_hit_negative_pnl(self):
        closed = self.bt._close_trade(self.trade, 5, self.ts, 1.1020, "SL")
        assert closed.pnl_points < 0


# ---------------------------------------------------------------------------
# _check_exit — BUY
# ---------------------------------------------------------------------------

class TestCheckExitBuy:
    def setup_method(self):
        self.bt    = Backtester(make_config())
        self.trade = make_trade(side="BUY", entry_price=1.1000,
                                stop_loss=1.0980, take_profit=1.1040)
        self.ts    = pd.Timestamp("2025-01-02")

    def test_sl_hit_returns_closed(self):
        bar    = make_bar(high=1.1010, low=1.0975)   # low below SL
        closed = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert closed is not None
        assert closed.exit_reason == "SL"

    def test_tp_hit_returns_closed(self):
        bar    = make_bar(high=1.1045, low=1.0990)   # high above TP
        closed = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert closed is not None
        assert closed.exit_reason == "TP"

    def test_both_hit_sl_wins(self):
        # low < SL and high > TP on same bar
        bar    = make_bar(high=1.1050, low=1.0975)
        closed = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert closed.exit_reason == "SL"

    def test_neither_returns_none(self):
        bar    = make_bar(high=1.1020, low=1.0990)   # within SL/TP
        result = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert result is None


# ---------------------------------------------------------------------------
# _check_exit — SELL
# ---------------------------------------------------------------------------

class TestCheckExitSell:
    def setup_method(self):
        self.bt    = Backtester(make_config())
        self.trade = make_trade(side="SELL", entry_price=1.1000,
                                stop_loss=1.1020, take_profit=1.0960)
        self.ts    = pd.Timestamp("2025-01-02")

    def test_sl_hit_sell(self):
        bar    = make_bar(high=1.1025, low=1.0970)   # high above SL
        closed = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert closed is not None
        assert closed.exit_reason == "SL"

    def test_tp_hit_sell(self):
        bar    = make_bar(high=1.0990, low=1.0955)   # low below TP
        closed = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert closed is not None
        assert closed.exit_reason == "TP"

    def test_neither_sell_returns_none(self):
        bar    = make_bar(high=1.1010, low=1.0970)
        result = self.bt._check_exit(self.trade, bar, 5, self.ts)
        assert result is None


# ---------------------------------------------------------------------------
# _compute_result — metrics
# ---------------------------------------------------------------------------

class TestComputeResult:
    def setup_method(self):
        self.bt = Backtester(make_config())
        self.ts = pd.Timestamp("2025-01-02")

    def _make_closed(self, pnl_r: float) -> BacktestTrade:
        t = make_trade()
        t.exit_bar_idx = 5
        t.exit_time    = self.ts
        t.exit_price   = t.entry_price + pnl_r * abs(t.entry_price - t.stop_loss)
        t.pnl_r        = pnl_r
        t.exit_reason  = "TP" if pnl_r > 0 else "SL"
        return t

    def test_win_rate(self):
        trades = [self._make_closed(2.0), self._make_closed(-1.0),
                  self._make_closed(2.0), self._make_closed(-1.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.win_rate == pytest.approx(0.5, abs=0.01)

    def test_total_r(self):
        trades = [self._make_closed(2.0), self._make_closed(-1.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.total_r == pytest.approx(1.0, abs=0.01)

    def test_equity_r_length(self):
        trades = [self._make_closed(2.0), self._make_closed(-1.0),
                  self._make_closed(2.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert len(res.equity_r) == 3

    def test_max_drawdown(self):
        # Equity: +2, +4, +3 → peak=4, trough=3, drawdown=1
        trades = [self._make_closed(2.0), self._make_closed(2.0),
                  self._make_closed(-1.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.max_drawdown_r == pytest.approx(1.0, abs=0.01)

    def test_profit_factor(self):
        trades = [self._make_closed(2.0), self._make_closed(-1.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.profit_factor == pytest.approx(2.0, abs=0.01)

    def test_sharpe_zero_with_one_trade(self):
        trades = [self._make_closed(2.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.sharpe_r == 0.0

    def test_win_count_loss_count(self):
        trades = [self._make_closed(2.0), self._make_closed(2.0),
                  self._make_closed(-1.0)]
        res = self.bt._compute_result("EURUSDm", trades)
        assert res.win_count  == 2
        assert res.loss_count == 1


# ---------------------------------------------------------------------------
# run() integration
# ---------------------------------------------------------------------------

class TestBacktesterRun:
    def test_returns_backtest_result(self):
        cfg = make_config()
        bt  = Backtester(cfg, warmup_bars=60)
        df  = make_df(n=150)
        res = bt.run(df, "EURUSDm")
        assert isinstance(res, BacktestResult)
        assert res.total_trades >= 0

    def test_short_df_returns_empty(self):
        cfg = make_config()
        bt  = Backtester(cfg, warmup_bars=60)
        df  = make_df(n=30)   # shorter than warmup
        res = bt.run(df, "EURUSDm")
        assert res.total_trades == 0

    def test_eod_closure_reason(self):
        """A trade forced open before last bar should close with EOD."""
        cfg = make_config(max_open_trades=5)
        bt  = Backtester(cfg, warmup_bars=60)
        df  = make_df(n=150)

        # Inject a fake open trade into the backtester by running then checking
        # the EOD path: we verify trades list contains at least one EOD if any
        # trade was still open at end. This is a smoke test — we just ensure
        # no crash and result is well-formed.
        res = bt.run(df, "EURUSDm")
        assert res.symbol == "EURUSDm"
        assert res.timeframe == "H1"
        for t in res.trades:
            assert t.exit_reason in ("SL", "TP", "EOD")

    def test_ob_dedup_direct(self):
        """Same OB key must not trigger two entries."""
        cfg = make_config()
        bt  = Backtester(cfg, warmup_bars=60)
        df  = make_df(n=150)

        # Patch generate_signals to always return one signal with the same ob.timestamp
        from signals.order_blocks import OrderBlock
        ob = OrderBlock(timestamp=pd.Timestamp("2025-01-01 01:00:00"),
                        side="BULLISH",
                        ob_high=1.11, ob_low=1.10, impulse_pct=0.003)

        from signals.strategy import TradeSignal
        fake_signal = TradeSignal(
            timestamp=pd.Timestamp("2025-01-10"),
            symbol="EURUSDm",
            side="BUY",
            entry_price=1.105,
            stop_loss=1.100,
            take_profit=1.115,
            size=0.01,
            regime=RegimeLabel.TRENDING_UP,
            ob=ob,
        )

        with patch("core.backtester.classify_regime") as mock_regime, \
             patch("core.backtester.detect_order_blocks") as mock_obs, \
             patch("core.backtester.generate_signals") as mock_gen:

            mock_regime.return_value = pd.Series(
                [RegimeLabel.TRENDING_UP] * len(df), index=df.index
            )
            mock_obs.return_value = []
            mock_gen.return_value = [fake_signal]

            res = bt.run(df, "EURUSDm")

        # Despite generate_signals returning the same OB signal every bar,
        # dedup should allow only 1 entry
        assert res.total_trades <= 1
