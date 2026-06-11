"""Phase 20 — Unit tests for core/walkforward.py.

Covers constructor validation, split_windows() math (no overlap, no
lookahead, remainder discard), run() integration with mocked Backtester,
param-grid optimization selection, aggregation metrics (mirroring
Backtester formulas), and edge cases (short data, zero trades).
"""
import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.backtester import Backtester, BacktestResult, BacktestTrade
from core.config import TradingConfig
from core.walkforward import WalkForwardRunner, WalkForwardResult, WalkForwardWindow


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


def make_df(n: int = 1000, trend: float = 0.0001) -> pd.DataFrame:
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


def make_closed_trade(pnl_r: float, ts="2025-01-10") -> BacktestTrade:
    return BacktestTrade(
        symbol="EURUSDm", side="BUY", entry_bar_idx=1,
        entry_time=pd.Timestamp(ts), entry_price=1.10,
        stop_loss=1.09, take_profit=1.12, size=0.01,
        exit_bar_idx=5, exit_time=pd.Timestamp(ts) + pd.Timedelta(hours=4),
        exit_price=1.10 + pnl_r * 0.01, exit_reason="TP" if pnl_r > 0 else "SL",
        pnl_points=pnl_r * 0.01, pnl_r=pnl_r,
    )


def make_bt_result(trades=None, symbol="EURUSDm") -> BacktestResult:
    trades = trades or []
    rs = [t.pnl_r for t in trades]
    wins = [r for r in rs if r > 0]
    return BacktestResult(
        symbol=symbol, timeframe="H1",
        total_trades=len(trades), win_count=len(wins),
        loss_count=len(trades) - len(wins),
        win_rate=len(wins) / len(trades) if trades else 0.0,
        total_r=sum(rs), avg_r_per_trade=sum(rs) / len(rs) if rs else 0.0,
        max_drawdown_r=0.0, sharpe_r=0.0, profit_factor=0.0,
        trades=trades,
    )


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructorValidation:
    def test_valid_defaults(self):
        runner = WalkForwardRunner(make_config())
        assert runner.train_bars == 500
        assert runner.test_bars == 250
        assert runner.warmup_bars == 60

    def test_train_bars_must_exceed_warmup(self):
        with pytest.raises(ValueError, match="train_bars"):
            WalkForwardRunner(make_config(), train_bars=60, warmup_bars=60)

    def test_test_bars_must_be_positive(self):
        with pytest.raises(ValueError, match="test_bars"):
            WalkForwardRunner(make_config(), test_bars=0)

    def test_warmup_bars_must_be_positive(self):
        with pytest.raises(ValueError, match="warmup_bars"):
            WalkForwardRunner(make_config(), warmup_bars=0)

    def test_unknown_param_grid_key_rejected(self):
        with pytest.raises(ValueError, match="not a TradingConfig field"):
            WalkForwardRunner(make_config(), param_grid={"nonsense_param": [1.0]})

    def test_empty_param_grid_values_rejected(self):
        with pytest.raises(ValueError, match="no candidate values"):
            WalkForwardRunner(make_config(), param_grid={"rr_ratio": []})

    def test_none_param_grid_means_no_optimization(self):
        runner = WalkForwardRunner(make_config(), param_grid=None)
        assert runner.param_grid == {}


# ---------------------------------------------------------------------------
# split_windows
# ---------------------------------------------------------------------------

class TestSplitWindows:
    def test_exact_fit_single_window(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        splits = runner.split_windows(750)
        assert splits == [(0, 500, 500, 750)]

    def test_too_short_returns_empty(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        assert runner.split_windows(749) == []

    def test_rolls_by_test_bars(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        splits = runner.split_windows(1000)
        assert splits == [(0, 500, 500, 750), (250, 750, 750, 1000)]

    def test_remainder_discarded(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        # 1100 bars: window 2 would need bars up to 1250 — discarded
        splits = runner.split_windows(1100)
        assert splits == [(0, 500, 500, 750), (250, 750, 750, 1000)]

    def test_test_windows_never_overlap(self):
        runner = WalkForwardRunner(make_config(), train_bars=300, test_bars=100)
        splits = runner.split_windows(2000)
        test_ranges = [(te_s, te_e) for _, _, te_s, te_e in splits]
        for (s1, e1), (s2, e2) in zip(test_ranges, test_ranges[1:]):
            assert e1 == s2  # contiguous, no gap, no overlap

    def test_train_end_equals_test_start_no_lookahead(self):
        runner = WalkForwardRunner(make_config(), train_bars=300, test_bars=100)
        for tr_s, tr_e, te_s, te_e in runner.split_windows(2000):
            assert tr_e == te_s          # train strictly before test
            assert tr_e - tr_s == 300    # full train window
            assert te_e - te_s == 100    # full test window


# ---------------------------------------------------------------------------
# run() — integration with mocked Backtester
# ---------------------------------------------------------------------------

class TestRunMocked:
    def test_short_df_returns_empty_result(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(100), "EURUSDm")
        assert result.window_count == 0
        assert result.total_trades == 0
        assert result.windows == []

    @patch("core.walkforward.Backtester")
    def test_one_backtest_run_per_window_without_optimization(self, MockBT):
        MockBT.return_value.run.return_value = make_bt_result()
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(1000), "EURUSDm")
        assert result.window_count == 2
        assert MockBT.return_value.run.call_count == 2  # test runs only

    @patch("core.walkforward.Backtester")
    def test_test_segment_includes_warmup_tail(self, MockBT):
        MockBT.return_value.run.return_value = make_bt_result()
        runner = WalkForwardRunner(
            make_config(), train_bars=500, test_bars=250, warmup_bars=60
        )
        df = make_df(750)
        runner.run(df, "EURUSDm")
        seg = MockBT.return_value.run.call_args[0][0]
        # Segment = warmup tail (60 bars of train) + test (250 bars)
        assert len(seg) == 310
        assert seg.index[0] == df.index[440]     # 500 - 60
        assert seg.index[-1] == df.index[749]

    @patch("core.walkforward.Backtester")
    def test_window_timestamps_recorded(self, MockBT):
        MockBT.return_value.run.return_value = make_bt_result()
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        df = make_df(750)
        result = runner.run(df, "EURUSDm")
        w = result.windows[0]
        assert w.train_start == df.index[0]
        assert w.train_end == df.index[499]
        assert w.test_start == df.index[500]
        assert w.test_end == df.index[749]
        assert w.params == {}
        assert w.train_result is None

    @patch("core.walkforward.Backtester")
    def test_trades_stitched_chronologically(self, MockBT):
        r1 = make_bt_result([make_closed_trade(1.0, "2025-01-05")])
        r2 = make_bt_result([make_closed_trade(-1.0, "2025-02-05")])
        MockBT.return_value.run.side_effect = [r1, r2]
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(1000), "EURUSDm")
        assert result.total_trades == 2
        assert [t.pnl_r for t in result.trades] == [1.0, -1.0]
        assert result.equity_r == [1.0, 0.0]


# ---------------------------------------------------------------------------
# Param-grid optimization
# ---------------------------------------------------------------------------

class TestOptimization:
    @patch("core.walkforward.Backtester")
    def test_best_train_params_applied_to_test(self, MockBT):
        # 3 combos on train: totals 0.5, 2.0, 1.0 → rr_ratio=2.0 wins.
        train_results = [
            make_bt_result([make_closed_trade(0.5)]),
            make_bt_result([make_closed_trade(2.0)]),
            make_bt_result([make_closed_trade(1.0)]),
        ]
        test_result = make_bt_result([make_closed_trade(1.5)])
        MockBT.return_value.run.side_effect = train_results + [test_result]

        runner = WalkForwardRunner(
            make_config(), train_bars=500, test_bars=250,
            param_grid={"rr_ratio": [1.5, 2.0, 3.0]},
        )
        result = runner.run(make_df(750), "EURUSDm")

        w = result.windows[0]
        assert w.params == {"rr_ratio": 2.0}
        assert w.train_result.total_r == 2.0
        # Test Backtester constructed with the winning rr_ratio
        test_cfg = MockBT.call_args_list[-1][0][0]
        assert test_cfg.rr_ratio == 2.0
        # Base config never mutated
        assert runner.config.rr_ratio == 2.0  # default from make_config

    @patch("core.walkforward.Backtester")
    def test_optimization_runs_grid_per_window(self, MockBT):
        MockBT.return_value.run.return_value = make_bt_result()
        runner = WalkForwardRunner(
            make_config(), train_bars=500, test_bars=250,
            param_grid={"rr_ratio": [1.5, 3.0], "sl_buffer": [0.0002, 0.0005]},
        )
        result = runner.run(make_df(1000), "EURUSDm")
        # 2 windows × (4 train combos + 1 test) = 10 runs
        assert result.window_count == 2
        assert MockBT.return_value.run.call_count == 10

    @patch("core.walkforward.Backtester")
    def test_tie_resolved_by_first_combo(self, MockBT):
        tied = make_bt_result([make_closed_trade(1.0)])
        MockBT.return_value.run.side_effect = [tied, tied, make_bt_result()]
        runner = WalkForwardRunner(
            make_config(), train_bars=500, test_bars=250,
            param_grid={"rr_ratio": [1.5, 3.0]},
        )
        result = runner.run(make_df(750), "EURUSDm")
        assert result.windows[0].params == {"rr_ratio": 1.5}


# ---------------------------------------------------------------------------
# Aggregation metrics
# ---------------------------------------------------------------------------

class TestAggregation:
    @patch("core.walkforward.Backtester")
    def test_metrics_match_backtester_formulas(self, MockBT):
        rs = [1.0, -1.0, 2.0, -0.5]
        r1 = make_bt_result([make_closed_trade(r) for r in rs[:2]])
        r2 = make_bt_result([make_closed_trade(r) for r in rs[2:]])
        MockBT.return_value.run.side_effect = [r1, r2]
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(1000), "EURUSDm")

        assert result.total_trades == 4
        assert result.win_count == 2
        assert result.loss_count == 2
        assert result.win_rate == 0.5
        assert result.total_r == pytest.approx(1.5)
        assert result.avg_r_per_trade == pytest.approx(0.375)
        # Equity: 1.0, 0.0, 2.0, 1.5 → max DD = 1.0
        assert result.max_drawdown_r == pytest.approx(1.0)
        # Profit factor: 3.0 / 1.5
        assert result.profit_factor == pytest.approx(2.0)
        # Sharpe: mean/std (sample), as in Backtester
        mean_r = sum(rs) / 4
        var = sum((r - mean_r) ** 2 for r in rs) / 3
        assert result.sharpe_r == pytest.approx(mean_r / math.sqrt(var), abs=1e-4)

    @patch("core.walkforward.Backtester")
    def test_pct_profitable_windows(self, MockBT):
        MockBT.return_value.run.side_effect = [
            make_bt_result([make_closed_trade(1.0)]),    # profitable
            make_bt_result([make_closed_trade(-1.0)]),   # not
        ]
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(1000), "EURUSDm")
        assert result.pct_profitable_windows == 0.5

    @patch("core.walkforward.Backtester")
    def test_zero_trades_across_all_windows(self, MockBT):
        MockBT.return_value.run.return_value = make_bt_result()
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner.run(make_df(1000), "EURUSDm")
        assert result.window_count == 2
        assert result.total_trades == 0
        assert result.total_r == 0.0
        assert result.pct_profitable_windows == 0.0
        assert len(result.windows) == 2  # windows preserved for inspection

    def test_summary_format(self):
        runner = WalkForwardRunner(make_config(), train_bars=500, test_bars=250)
        result = runner._empty_result("EURUSDm")
        s = result.summary()
        assert "EURUSDm WF" in s
        assert "windows=0" in s


# ---------------------------------------------------------------------------
# Real-pipeline smoke test (no mocks)
# ---------------------------------------------------------------------------

class TestRealPipelineSmoke:
    def test_runs_end_to_end_on_synthetic_data(self):
        """Full unmocked pipeline must not raise and must produce windows."""
        runner = WalkForwardRunner(
            make_config(), train_bars=200, test_bars=100, warmup_bars=60
        )
        result = runner.run(make_df(500, trend=0.0003), "EURUSDm")
        assert isinstance(result, WalkForwardResult)
        assert result.window_count == 3
        assert result.window_count == len(result.windows)
        # Every trade must have closed inside its test segment (OOS)
        for w in result.windows:
            for t in w.test_result.trades:
                assert t.entry_time >= w.test_start
                assert t.entry_time <= w.test_end
