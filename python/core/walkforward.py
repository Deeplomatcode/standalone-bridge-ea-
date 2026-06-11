"""Phase 20 — Walk-forward out-of-sample validation.

Splits a historical OHLCV DataFrame into rolling train/test windows and runs
the existing bar-by-bar ``Backtester`` on each *test* segment, so every
reported trade is out-of-sample. Optionally grid-searches strategy parameters
on each train window and applies the winner to the following test window
(classic walk-forward optimization).

Window layout (index-based, step = test_bars)::

    |--- train (train_bars) ---|--- test (test_bars) ---|
                  |--- train ---|--- test ---|
                                ...

Test segments never overlap and are strictly ordered, so stitching their
trades yields a single chronological out-of-sample equity curve.

Lookahead policy:
- Train-window optimization sees ONLY train bars.
- Each test run receives the last ``warmup_bars`` bars of its train window as
  warmup context (the Backtester skips them for entries), so regime/OB state
  carries in without ever consuming a future bar.

Usage::

    runner = WalkForwardRunner(config, train_bars=500, test_bars=250)
    result = runner.run(df, "EURUSDm")
    print(result.summary())

With optimization::

    runner = WalkForwardRunner(
        config, train_bars=500, test_bars=250,
        param_grid={"rr_ratio": [1.5, 2.0, 3.0], "sl_buffer": [0.0002, 0.0005]},
    )
"""
from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core.backtester import Backtester, BacktestResult, BacktestTrade
from core.config import TradingConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardWindow:
    """One train/test split and its out-of-sample result.

    Attributes:
        window_idx:   0-based window number, chronological.
        train_start:  Timestamp of first train bar.
        train_end:    Timestamp of last train bar (inclusive).
        test_start:   Timestamp of first test bar.
        test_end:     Timestamp of last test bar (inclusive).
        params:       Strategy params applied to the test segment
                      (overrides only; empty dict = config defaults).
        train_result: BacktestResult on the train window with the chosen
                      params. None when no optimization was requested.
        test_result:  Out-of-sample BacktestResult on the test segment.
    """
    window_idx:   int
    train_start:  pd.Timestamp
    train_end:    pd.Timestamp
    test_start:   pd.Timestamp
    test_end:     pd.Timestamp
    params:       Dict[str, float]
    test_result:  BacktestResult
    train_result: Optional[BacktestResult] = None


@dataclass
class WalkForwardResult:
    """Aggregate out-of-sample results across all walk-forward windows.

    All metrics are computed over the stitched, chronological list of
    out-of-sample trades — identical formulas to BacktestResult so numbers
    are directly comparable with a plain backtest.
    """
    symbol:                  str
    timeframe:               str
    train_bars:              int
    test_bars:               int
    window_count:            int
    total_trades:            int
    win_count:               int
    loss_count:              int
    win_rate:                float
    total_r:                 float
    avg_r_per_trade:         float
    max_drawdown_r:          float
    sharpe_r:                float
    profit_factor:           float
    pct_profitable_windows:  float
    windows:                 List[WalkForwardWindow] = field(default_factory=list)
    trades:                  List[BacktestTrade]     = field(default_factory=list)
    equity_r:                List[float]             = field(default_factory=list)

    def summary(self) -> str:
        """One-line human-readable result."""
        return (
            f"{self.symbol} WF | windows={self.window_count} "
            f"trades={self.total_trades} win%={self.win_rate:.1%} "
            f"totalR={self.total_r:.2f} maxDD={self.max_drawdown_r:.2f}R "
            f"sharpe={self.sharpe_r:.2f} "
            f"profitableWindows={self.pct_profitable_windows:.0%}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class WalkForwardRunner:
    """Rolling walk-forward validation on top of ``Backtester``.

    Args:
        config:      Base TradingConfig. Param-grid overrides are applied
                     per-window via dataclasses.replace (config is never
                     mutated).
        train_bars:  Bars per train window. Must be > warmup_bars.
        test_bars:   Bars per test window (also the roll step). Must be >= 1.
        warmup_bars: Warmup passed to each Backtester run; also the slice of
                     train tail prepended to every test segment for
                     regime/OB context. Default 60.
        param_grid:  Optional dict of {config_field: [candidates]}. When
                     given, every combination is backtested on each train
                     window and the combo with the highest total_r (ties:
                     first in iteration order) is applied to the test window.
                     Keys must be existing TradingConfig fields.

    Raises:
        ValueError: on invalid window sizes or unknown param_grid keys.
    """

    def __init__(
        self,
        config:      TradingConfig,
        train_bars:  int = 500,
        test_bars:   int = 250,
        warmup_bars: int = 60,
        param_grid:  Optional[Dict[str, List[float]]] = None,
    ) -> None:
        if warmup_bars < 1:
            raise ValueError(f"warmup_bars must be >= 1, got {warmup_bars}")
        if train_bars <= warmup_bars:
            raise ValueError(
                f"train_bars ({train_bars}) must be > warmup_bars ({warmup_bars})"
            )
        if test_bars < 1:
            raise ValueError(f"test_bars must be >= 1, got {test_bars}")
        if param_grid:
            for key in param_grid:
                if not hasattr(config, key):
                    raise ValueError(f"param_grid key '{key}' is not a TradingConfig field")
                if not param_grid[key]:
                    raise ValueError(f"param_grid key '{key}' has no candidate values")

        self.config      = config
        self.train_bars  = train_bars
        self.test_bars   = test_bars
        self.warmup_bars = warmup_bars
        self.param_grid  = param_grid or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, df: pd.DataFrame, symbol: str) -> WalkForwardResult:
        """Run walk-forward validation on *df* for *symbol*.

        Args:
            df:     OHLCV DataFrame (open/high/low/close/volume, DatetimeIndex).
            symbol: Broker symbol string, e.g. "EURUSDm".

        Returns:
            WalkForwardResult. Empty (window_count=0) if df is too short for
            a single full train+test window.
        """
        splits = self.split_windows(len(df))
        if not splits:
            logger.warning(
                f"[{symbol}] DataFrame too short ({len(df)} bars) for one "
                f"walk-forward window (train={self.train_bars} + "
                f"test={self.test_bars}). Returning empty result."
            )
            return self._empty_result(symbol)

        windows: List[WalkForwardWindow] = []
        for w_idx, (tr_s, tr_e, te_s, te_e) in enumerate(splits):
            train_df = df.iloc[tr_s:tr_e]

            params: Dict[str, float] = {}
            train_result: Optional[BacktestResult] = None
            if self.param_grid:
                params, train_result = self._optimize(train_df, symbol)

            cfg = replace(self.config, **params) if params else self.config

            # Test segment: last warmup_bars of train prepended as context.
            # Backtester skips those bars for entries, so all trades are OOS.
            seg = df.iloc[te_s - self.warmup_bars : te_e]
            bt = Backtester(cfg, warmup_bars=self.warmup_bars)
            test_result = bt.run(seg, symbol)

            windows.append(WalkForwardWindow(
                window_idx=w_idx,
                train_start=df.index[tr_s],
                train_end=df.index[tr_e - 1],
                test_start=df.index[te_s],
                test_end=df.index[te_e - 1],
                params=params,
                test_result=test_result,
                train_result=train_result,
            ))
            logger.info(
                f"[{symbol}] WF window {w_idx}: "
                f"test {df.index[te_s]} → {df.index[te_e - 1]}  "
                f"trades={test_result.total_trades} "
                f"totalR={test_result.total_r:.2f} params={params or 'default'}"
            )

        return self._aggregate(symbol, windows)

    def split_windows(self, n_bars: int) -> List[Tuple[int, int, int, int]]:
        """Compute rolling window boundaries for *n_bars* of data.

        Returns:
            List of (train_start, train_end, test_start, test_end) iloc
            bounds — start inclusive, end exclusive. train_end == test_start.
            Only full test windows are produced; a trailing remainder shorter
            than test_bars is discarded.
        """
        splits: List[Tuple[int, int, int, int]] = []
        start = 0
        while start + self.train_bars + self.test_bars <= n_bars:
            tr_s = start
            tr_e = start + self.train_bars
            te_s = tr_e
            te_e = te_s + self.test_bars
            splits.append((tr_s, tr_e, te_s, te_e))
            start += self.test_bars
        return splits

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _optimize(
        self, train_df: pd.DataFrame, symbol: str
    ) -> Tuple[Dict[str, float], BacktestResult]:
        """Grid-search param_grid on *train_df*; return best combo + result.

        Best = highest total_r. Ties resolved by iteration order (first
        combination wins), which keeps results deterministic.
        """
        keys   = list(self.param_grid.keys())
        combos = list(itertools.product(*(self.param_grid[k] for k in keys)))

        best_params: Dict[str, float] = dict(zip(keys, combos[0]))
        best_result: Optional[BacktestResult] = None

        for combo in combos:
            params = dict(zip(keys, combo))
            cfg = replace(self.config, **params)
            bt = Backtester(cfg, warmup_bars=self.warmup_bars)
            result = bt.run(train_df, symbol)
            if best_result is None or result.total_r > best_result.total_r:
                best_params = params
                best_result = result

        assert best_result is not None  # combos is never empty (validated)
        logger.debug(
            f"[{symbol}] Train optimization: best={best_params} "
            f"totalR={best_result.total_r:.2f} over {len(combos)} combos"
        )
        return best_params, best_result

    def _aggregate(
        self, symbol: str, windows: List[WalkForwardWindow]
    ) -> WalkForwardResult:
        """Stitch OOS trades chronologically and compute aggregate metrics.

        Formulas mirror Backtester._compute_result exactly so walk-forward
        numbers are directly comparable with plain backtest numbers.
        """
        trades: List[BacktestTrade] = []
        for w in windows:
            trades.extend(w.test_result.trades)

        profitable = sum(1 for w in windows if w.test_result.total_r > 0)
        pct_profitable = profitable / len(windows)

        if not trades:
            empty = self._empty_result(symbol)
            empty.window_count = len(windows)
            empty.windows = windows
            empty.pct_profitable_windows = round(pct_profitable, 4)
            return empty

        rs     = [t.pnl_r for t in trades]
        wins   = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]

        win_rate = len(wins) / len(trades)
        total_r  = sum(rs)
        avg_r    = total_r / len(trades)

        equity_r: List[float] = []
        running = 0.0
        for r in rs:
            running += r
            equity_r.append(round(running, 4))

        peak   = equity_r[0]
        max_dd = 0.0
        for val in equity_r:
            peak   = max(peak, val)
            max_dd = max(max_dd, peak - val)

        if len(rs) >= 2:
            mean_r   = sum(rs) / len(rs)
            variance = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
            std_r    = math.sqrt(variance) if variance > 0 else 0.0
            sharpe   = mean_r / std_r if std_r > 1e-10 else 0.0
        else:
            sharpe = 0.0

        gross_profit  = sum(wins)
        gross_loss    = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

        return WalkForwardResult(
            symbol=symbol,
            timeframe=self.config.timeframe,
            train_bars=self.train_bars,
            test_bars=self.test_bars,
            window_count=len(windows),
            total_trades=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=round(win_rate, 4),
            total_r=round(total_r, 4),
            avg_r_per_trade=round(avg_r, 4),
            max_drawdown_r=round(max_dd, 4),
            sharpe_r=round(sharpe, 4),
            profit_factor=round(profit_factor, 4),
            pct_profitable_windows=round(pct_profitable, 4),
            windows=windows,
            trades=trades,
            equity_r=equity_r,
        )

    def _empty_result(self, symbol: str) -> WalkForwardResult:
        return WalkForwardResult(
            symbol=symbol,
            timeframe=self.config.timeframe,
            train_bars=self.train_bars,
            test_bars=self.test_bars,
            window_count=0,
            total_trades=0,
            win_count=0,
            loss_count=0,
            win_rate=0.0,
            total_r=0.0,
            avg_r_per_trade=0.0,
            max_drawdown_r=0.0,
            sharpe_r=0.0,
            profit_factor=0.0,
            pct_profitable_windows=0.0,
        )
