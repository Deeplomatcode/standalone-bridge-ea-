"""Phase 17 — Bar-by-bar strategy backtester.

Simulates the full signal pipeline on historical OHLCV data without touching
a live broker. Pre-computes regime and order blocks on the full DataFrame,
then iterates bar by bar — exits first, entries second.

Lookahead note (MVP): OBs are detected on the full df for performance.
A timestamp filter (`ob.timestamp < current bar`) prevents future OBs from
triggering entries. Strict walk-forward is Phase 20+.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from core.config import TradingConfig
from signals.order_blocks import OrderBlock, detect_order_blocks, mark_mitigated
from signals.regime import classify_regime
from signals.session import filter_by_session
from signals.strategy import generate_signals
from risk.lot_sizer import size_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    """A single simulated trade, open or closed."""

    symbol:        str
    side:          str               # "BUY" or "SELL"
    entry_bar_idx: int
    entry_time:    pd.Timestamp
    entry_price:   float
    stop_loss:     float
    take_profit:   float
    size:          float
    exit_bar_idx:  int                        = -1
    exit_time:     Optional[pd.Timestamp]     = None
    exit_price:    float                      = 0.0
    exit_reason:   str                        = ""    # "SL", "TP", "EOD"
    pnl_points:    float                      = 0.0
    pnl_r:         float                      = 0.0


@dataclass
class BacktestResult:
    """Aggregate results of a single-symbol backtest run."""

    symbol:          str
    timeframe:       str
    total_trades:    int
    win_count:       int
    loss_count:      int
    win_rate:        float
    total_r:         float
    avg_r_per_trade: float
    max_drawdown_r:  float
    sharpe_r:        float
    profit_factor:   float
    trades:          List[BacktestTrade] = field(default_factory=list)
    equity_r:        List[float]         = field(default_factory=list)

    def summary(self) -> str:
        """One-line human-readable result."""
        return (
            f"{self.symbol} | trades={self.total_trades} "
            f"win%={self.win_rate:.1%} totalR={self.total_r:.2f} "
            f"maxDD={self.max_drawdown_r:.2f}R sharpe={self.sharpe_r:.2f}"
        )


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class Backtester:
    """Bar-by-bar strategy simulator.

    Pre-computes regime and order blocks on the full DataFrame for speed,
    then iterates bar by bar — checking exits first, then entries. OBs with
    ``timestamp >= current bar`` are excluded to prevent lookahead.

    Args:
        config:       TradingConfig supplying strategy and risk parameters.
        warmup_bars:  Leading bars to skip (regime/OB calculation warmup).
                      Default 60.
    """

    def __init__(self, config: TradingConfig, warmup_bars: int = 60) -> None:
        self.config = config
        self.warmup_bars = warmup_bars

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, df: pd.DataFrame, symbol: str) -> BacktestResult:
        """Run a bar-by-bar backtest on *df* for *symbol*.

        Args:
            df:      OHLCV DataFrame (columns: open, high, low, close, volume;
                     DatetimeIndex).
            symbol:  Broker symbol string, e.g. "EURUSDm".

        Returns:
            BacktestResult with all trade records and aggregate metrics.
        """
        if len(df) <= self.warmup_bars:
            logger.warning(
                f"[{symbol}] DataFrame too short ({len(df)} bars) for "
                f"warmup={self.warmup_bars}. Returning empty result."
            )
            return self._empty_result(symbol)

        # Pre-compute regime for full df — causal, no lookahead
        all_regimes = classify_regime(
            df, adx_trend_threshold=self.config.adx_trend_threshold
        )

        # Pre-compute OBs on full df (MVP: minor lookahead in OB detection)
        all_obs = detect_order_blocks(df)

        open_trades: List[BacktestTrade] = []
        closed_trades: List[BacktestTrade] = []
        triggered_ob_keys: set = set()   # prevent re-entry on same OB

        for i in range(self.warmup_bars, len(df)):
            bar = df.iloc[i]
            ts  = df.index[i]

            # 1. Check exits for all open positions
            still_open: List[BacktestTrade] = []
            for trade in open_trades:
                closed = self._check_exit(trade, bar, i, ts)
                if closed is not None:
                    closed_trades.append(closed)
                else:
                    still_open.append(trade)
            open_trades = still_open

            # 2. Generate entries if capacity allows
            if len(open_trades) < self.config.max_open_trades:
                # Only OBs formed before current bar (timestamp filter)
                bar_obs = [ob for ob in all_obs if ob.timestamp < ts]

                # Regime and data sliced up to current bar
                regime_slice = all_regimes.iloc[: i + 1]
                window = df.iloc[: i + 1]

                signals = generate_signals(
                    window,
                    symbol,
                    bar_obs,
                    regime_slice,
                    size=self.config.lot_size,
                    sl_buffer=self.config.sl_buffer,
                    rr_ratio=self.config.rr_ratio,
                )

                # Session filter (Phase 18)
                if self.config.session_filter_enabled:
                    signals = filter_by_session(signals)

                # Risk-based lot sizing (Phase 19)
                signals = size_signals(signals, self.config)

                for signal in signals:
                    ob_key = str(signal.ob.timestamp)
                    if ob_key in triggered_ob_keys:
                        continue
                    if len(open_trades) >= self.config.max_open_trades:
                        break
                    triggered_ob_keys.add(ob_key)
                    trade = BacktestTrade(
                        symbol=symbol,
                        side=signal.side,
                        entry_bar_idx=i,
                        entry_time=ts,
                        entry_price=signal.entry_price,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        size=signal.size,
                    )
                    open_trades.append(trade)
                    logger.debug(
                        f"[{symbol}] Entry bar={i} {signal.side} "
                        f"@ {signal.entry_price:.5f}"
                    )

        # Close remaining open positions at end of data
        last_bar = df.iloc[-1]
        last_ts  = df.index[-1]
        for trade in open_trades:
            eod = self._close_trade(
                trade, len(df) - 1, last_ts, last_bar["close"], "EOD"
            )
            closed_trades.append(eod)

        logger.info(f"[{symbol}] Backtest complete: {len(closed_trades)} trades")
        return self._compute_result(symbol, closed_trades)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_exit(
        self,
        trade:   BacktestTrade,
        bar:     pd.Series,
        bar_idx: int,
        ts:      pd.Timestamp,
    ) -> Optional[BacktestTrade]:
        """Check SL/TP for one trade against *bar*.

        SL check runs first — if both SL and TP are hit on the same bar,
        SL wins (conservative worst-case assumption).

        Returns closed BacktestTrade on hit, or None if still open.
        """
        if trade.side == "BUY":
            sl_hit = bar["low"]  <= trade.stop_loss
            tp_hit = bar["high"] >= trade.take_profit
        else:  # SELL
            sl_hit = bar["high"] >= trade.stop_loss
            tp_hit = bar["low"]  <= trade.take_profit

        if sl_hit:
            return self._close_trade(trade, bar_idx, ts, trade.stop_loss,  "SL")
        if tp_hit:
            return self._close_trade(trade, bar_idx, ts, trade.take_profit, "TP")
        return None

    def _close_trade(
        self,
        trade:      BacktestTrade,
        bar_idx:    int,
        ts:         pd.Timestamp,
        exit_price: float,
        reason:     str,
    ) -> BacktestTrade:
        """Return a new closed BacktestTrade with P&L computed."""
        if trade.side == "BUY":
            pnl_points = exit_price - trade.entry_price
        else:
            pnl_points = trade.entry_price - exit_price

        risk   = abs(trade.entry_price - trade.stop_loss)
        pnl_r  = pnl_points / risk if risk > 1e-10 else 0.0

        return BacktestTrade(
            symbol=trade.symbol,
            side=trade.side,
            entry_bar_idx=trade.entry_bar_idx,
            entry_time=trade.entry_time,
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            size=trade.size,
            exit_bar_idx=bar_idx,
            exit_time=ts,
            exit_price=exit_price,
            exit_reason=reason,
            pnl_points=round(pnl_points, 5),
            pnl_r=round(pnl_r, 4),
        )

    def _compute_result(
        self, symbol: str, trades: List[BacktestTrade]
    ) -> BacktestResult:
        """Compute aggregate metrics from a list of closed trades."""
        if not trades:
            return self._empty_result(symbol)

        rs     = [t.pnl_r for t in trades]
        wins   = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]

        win_rate = len(wins) / len(trades)
        total_r  = sum(rs)
        avg_r    = total_r / len(trades)

        # Cumulative equity curve in R
        equity_r: List[float] = []
        running = 0.0
        for r in rs:
            running += r
            equity_r.append(round(running, 4))

        # Max drawdown on equity curve
        peak   = equity_r[0]
        max_dd = 0.0
        for val in equity_r:
            peak   = max(peak, val)
            max_dd = max(max_dd, peak - val)

        # Trade-based Sharpe (no annualisation — valid for trade-series comparison)
        if len(rs) >= 2:
            mean_r   = sum(rs) / len(rs)
            variance = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
            std_r    = math.sqrt(variance) if variance > 0 else 0.0
            sharpe   = mean_r / std_r if std_r > 1e-10 else 0.0
        else:
            sharpe = 0.0

        # Profit factor
        gross_profit  = sum(wins)
        gross_loss    = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

        return BacktestResult(
            symbol=symbol,
            timeframe=self.config.timeframe,
            total_trades=len(trades),
            win_count=len(wins),
            loss_count=len(losses),
            win_rate=round(win_rate, 4),
            total_r=round(total_r, 4),
            avg_r_per_trade=round(avg_r, 4),
            max_drawdown_r=round(max_dd, 4),
            sharpe_r=round(sharpe, 4),
            profit_factor=round(profit_factor, 4),
            trades=trades,
            equity_r=equity_r,
        )

    def _empty_result(self, symbol: str) -> BacktestResult:
        return BacktestResult(
            symbol=symbol,
            timeframe=self.config.timeframe,
            total_trades=0,
            win_count=0,
            loss_count=0,
            win_rate=0.0,
            total_r=0.0,
            avg_r_per_trade=0.0,
            max_drawdown_r=0.0,
            sharpe_r=0.0,
            profit_factor=0.0,
        )
