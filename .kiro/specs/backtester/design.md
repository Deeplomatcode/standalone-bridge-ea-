# Design

## Files

```
python/core/backtester.py           ← NEW — BacktestTrade, BacktestResult, Backtester
python/tests/test_backtester.py     ← NEW — pytest unit tests (20-25)
```

Do NOT modify any existing file.

---

## Verified API Reference

Read these files before writing any code. Field names confirmed from actual source.

### `python/signals/strategy.py`
```python
@dataclass
class TradeSignal:
    timestamp:   pd.Timestamp
    symbol:      str
    side:        str           # "BUY" or "SELL"
    entry_price: float
    stop_loss:   float         # NOT sl
    take_profit: float         # NOT tp
    size:        float
    regime:      RegimeLabel
    ob:          OrderBlock    # the OrderBlock that triggered this signal
    comment:     str = ""

def generate_signals(
    df: pd.DataFrame,
    symbol: str,
    obs: List[OrderBlock],
    regimes: pd.Series,
    size: float = 0.01,
    sl_buffer: float = 0.0002,
    rr_ratio: float = 2.0,
    tolerance: float = 0.0002,
) -> List[TradeSignal]: ...
```

### `python/signals/regime.py`
```python
def classify_regime(
    df: pd.DataFrame,
    adx_trend_threshold: float = 25.0,
    **kwargs,
) -> pd.Series: ...        # Series of RegimeLabel, indexed same as df
```

### `python/signals/order_blocks.py`
```python
@dataclass
class OrderBlock:
    timestamp:   pd.Timestamp
    side:        str        # "BULLISH" or "BEARISH" (NOT ob_type)
    ob_high:     float
    ob_low:      float
    impulse_pct: float      # required — impulse size as fraction of price
    active:      bool = True
    # NOTE: no `triggered` field exists in the real code

def detect_order_blocks(df: pd.DataFrame, **kwargs) -> List[OrderBlock]: ...
def mark_mitigated(obs: List[OrderBlock], df: pd.DataFrame) -> List[OrderBlock]: ...
```

### `python/core/config.py`
```python
@dataclass
class TradingConfig:
    max_open_trades: int = 5
    lot_size: float = 0.01
    sl_buffer: float = 0.0002
    rr_ratio: float = 2.0
    adx_trend_threshold: float = 25.0
    timeframe: str = "H1"
    # ... other fields
```

---

## `python/core/backtester.py` — Complete Implementation

```python
from __future__ import annotations

import logging
import math
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from core.config import TradingConfig
from signals.regime import classify_regime
from signals.order_blocks import detect_order_blocks, mark_mitigated, OrderBlock
from signals.strategy import generate_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class BacktestTrade:
    """A single simulated trade, open or closed."""
    symbol:        str
    side:          str            # "BUY" or "SELL"
    entry_bar_idx: int
    entry_time:    pd.Timestamp
    entry_price:   float
    stop_loss:     float
    take_profit:   float
    size:          float
    exit_bar_idx:  int            = -1
    exit_time:     Optional[pd.Timestamp] = None
    exit_price:    float          = 0.0
    exit_reason:   str            = ""   # "SL", "TP", "EOD"
    pnl_points:    float          = 0.0
    pnl_r:         float          = 0.0


@dataclass
class BacktestResult:
    """Aggregate results of a single-symbol backtest run."""
    symbol:         str
    timeframe:      str
    total_trades:   int
    win_count:      int
    loss_count:     int
    win_rate:       float
    total_r:        float
    avg_r_per_trade: float
    max_drawdown_r: float
    sharpe_r:       float
    profit_factor:  float
    trades:         List[BacktestTrade] = field(default_factory=list)
    equity_r:       List[float]         = field(default_factory=list)

    def summary(self) -> str:
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

    Pre-computes regime and order blocks on the full DataFrame, then iterates
    bar by bar — checking exits first, then entries. OBs with timestamp >=
    current bar are excluded to prevent lookahead.

    Args:
        config:       TradingConfig supplying strategy and risk parameters.
        warmup_bars:  Number of leading bars to skip (regime/OB warmup).
                      Default 60.
    """

    def __init__(self, config: TradingConfig, warmup_bars: int = 60) -> None:
        self.config = config
        self.warmup_bars = warmup_bars

    def run(self, df: pd.DataFrame, symbol: str) -> BacktestResult:
        """Run a bar-by-bar backtest on *df* for *symbol*.

        Args:
            df:      OHLCV DataFrame from update_ohlcv / load_ohlcv.
                     Must have columns: open, high, low, close, volume.
                     Index must be DatetimeIndex.
            symbol:  Broker symbol string, e.g. "EURUSDm".

        Returns:
            BacktestResult with all trade records and aggregate metrics.
        """
        if len(df) <= self.warmup_bars:
            logger.warning(
                f"[{symbol}] DataFrame too short ({len(df)} bars) for warmup={self.warmup_bars}. "
                f"Returning empty result."
            )
            return self._empty_result(symbol)

        # Pre-compute regime for full df (causal — no lookahead)
        all_regimes = classify_regime(df, adx_trend_threshold=self.config.adx_trend_threshold)

        # Pre-compute OBs on full df (minor lookahead in OB detection — MVP)
        all_obs = detect_order_blocks(df)

        open_trades: List[BacktestTrade] = []
        closed_trades: List[BacktestTrade] = []
        triggered_ob_keys: set = set()    # prevent re-entry on same OB

        for i in range(self.warmup_bars, len(df)):
            bar = df.iloc[i]
            ts  = df.index[i]

            # 1. Check exits for open positions
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
                # Filter OBs to those that formed before current bar
                bar_obs = [ob for ob in all_obs if ob.timestamp < ts]

                # Slice regime Series up to current bar for generate_signals
                regime_slice = all_regimes.iloc[: i + 1]

                # Slice df up to current bar for signal generation
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
                    logger.debug(f"[{symbol}] Entry bar={i} {signal.side} @ {signal.entry_price:.5f}")

        # Close remaining open trades at end of data
        last_bar = df.iloc[-1]
        last_ts  = df.index[-1]
        for trade in open_trades:
            eod = self._close_trade(trade, len(df) - 1, last_ts, last_bar["close"], "EOD")
            closed_trades.append(eod)

        logger.info(f"[{symbol}] Backtest complete: {len(closed_trades)} trades")
        return self._compute_result(symbol, closed_trades)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_exit(
        self,
        trade: BacktestTrade,
        bar:   pd.Series,
        bar_idx: int,
        ts:    pd.Timestamp,
    ) -> Optional[BacktestTrade]:
        """Check SL/TP for a single trade against *bar*. Return closed trade or None."""
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

        risk = abs(trade.entry_price - trade.stop_loss)
        pnl_r = pnl_points / risk if risk > 1e-10 else 0.0

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

    def _compute_result(self, symbol: str, trades: List[BacktestTrade]) -> BacktestResult:
        """Compute aggregate metrics from closed trades."""
        if not trades:
            return self._empty_result(symbol)

        rs = [t.pnl_r for t in trades]
        wins   = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]

        win_rate = len(wins) / len(trades)
        total_r  = sum(rs)
        avg_r    = total_r / len(trades)

        # Cumulative equity curve
        equity_r: List[float] = []
        running = 0.0
        for r in rs:
            running += r
            equity_r.append(round(running, 4))

        # Max drawdown on equity curve
        peak = equity_r[0]
        max_dd = 0.0
        for val in equity_r:
            peak = max(peak, val)
            dd = peak - val
            max_dd = max(max_dd, dd)

        # Sharpe (trade-based, no annualisation)
        if len(rs) >= 2:
            mean_r = sum(rs) / len(rs)
            variance = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = mean_r / std_r if std_r > 1e-10 else 0.0
        else:
            sharpe = 0.0

        # Profit factor
        gross_profit = sum(wins)
        gross_loss   = abs(sum(losses))
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
```

---

## Tests — `python/tests/test_backtester.py`

### Synthetic data helper

```python
import pandas as pd
import numpy as np

def make_trending_df(n: int = 200) -> pd.DataFrame:
    """Rising price series with some volatility — should produce TRENDING_UP regime."""
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    close = 1.1 + np.cumsum(np.random.normal(0.0001, 0.0003, n))
    high  = close + 0.0005
    low   = close - 0.0005
    return pd.DataFrame(
        {"open": close - 0.0002, "high": high, "low": low, "close": close, "volume": 1000},
        index=idx,
    )

def make_flat_df(n: int = 200) -> pd.DataFrame:
    """Flat / sideways price — produces RANGING regime, fewer signals."""
    idx = pd.date_range("2025-01-01", periods=n, freq="h")
    close = np.ones(n) * 1.1 + np.random.normal(0, 0.0001, n)
    high  = close + 0.0003
    low   = close - 0.0003
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1000},
        index=idx,
    )
```

### Required test cases

**BacktestTrade defaults:**
- Default `exit_bar_idx` is -1
- Default `pnl_r` is 0.0

**BacktestResult:**
- `summary()` returns a non-empty string
- Empty result has `total_trades == 0` and `win_rate == 0.0`

**`_close_trade()` — BUY:**
- Exit above entry → positive `pnl_points`
- Exit below entry → negative `pnl_points`
- `pnl_r ≈ +2.0` when exit = take_profit with 2:1 RR
- `pnl_r ≈ -1.0` when exit = stop_loss
- `exit_reason` stored correctly

**`_close_trade()` — SELL:**
- Exit below entry → positive `pnl_points`
- Exit above entry → negative `pnl_points`

**`_check_exit()` — BUY:**
- Bar low ≤ SL → SL hit, returns closed trade
- Bar high ≥ TP → TP hit, returns closed trade
- Both same bar → SL wins (exit_reason = "SL")
- Neither → returns None

**`_check_exit()` — SELL:**
- Bar high ≥ SL → SL hit
- Bar low ≤ TP → TP hit
- Neither → returns None

**`_compute_result()` metrics:**
- `win_rate` = wins / total
- `total_r` = sum of pnl_r
- `max_drawdown_r` correct for a sequence with a mid-run loss
- `equity_r` length equals number of trades
- `profit_factor` = gross_profit / gross_loss
- `sharpe_r` = 0.0 when only 1 trade

**`run()` integration:**
- Returns `BacktestResult` with `total_trades >= 0`
- DataFrame shorter than warmup → returns empty result (0 trades)
- EOD closure: if trade open at end of data, `exit_reason == "EOD"`
- Same OB not triggered twice (triggered_ob_keys dedup)
