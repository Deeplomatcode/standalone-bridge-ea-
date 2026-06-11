# Requirements

## Introduction

The backtester (`python/core/backtester.py`) runs the full signal pipeline on historical OHLCV data
bar-by-bar and simulates trade fills, exits, and P&L without touching a live broker. It is the
primary tool for validating strategy parameters before connecting to a demo account.

---

## Requirements

### 1. Bar-by-Bar Simulation

**Given** a historical OHLCV DataFrame and a symbol,
**When** `backtester.run(df, symbol)` is called,
**Then** it iterates each bar from `warmup_bars` onward, checking exits first then entries, and
returns a `BacktestResult` when the DataFrame is exhausted.

---

### 2. No Lookahead on Regime or Signal Logic

**Given** at bar `i` only data up to and including bar `i` should be visible,
**When** regime and signal logic runs for bar `i`,
**Then**:
- Regime for bar `i` is taken from a pre-computed `pd.Series` — each bar's regime only uses
  its own history (no lookahead, `classify_regime` is causal).
- OBs used at bar `i` are filtered to those whose `timestamp < df.index[i]` — OBs that form
  in the future are not visible.

> **MVP note:** OBs are pre-computed on the full df for performance. The timestamp filter prevents
> future OBs from triggering entries. This is explicitly an MVP approach; strict walk-forward
> is Phase 20+.

---

### 3. Exit Logic — SL and TP

**Given** an open simulated position,
**When** processing each bar,
**Then**:
- **BUY — SL hit**: `bar.low ≤ position.stop_loss` → exit at `stop_loss` price.
- **BUY — TP hit**: `bar.high ≥ position.take_profit` → exit at `take_profit` price.
- **SELL — SL hit**: `bar.high ≥ position.stop_loss` → exit at `stop_loss` price.
- **SELL — TP hit**: `bar.low ≤ position.take_profit` → exit at `take_profit` price.
- **Both SL and TP on same bar**: SL wins (conservative — worst-case assumption).
- **End of data**: all open positions closed at last bar's `close` price, reason `"EOD"`.

---

### 4. Entry Logic — OB Retest

**Given** the strategy uses order block retests,
**When** a signal is generated at bar `i`,
**Then**:
- The same OB (identified by `ob.timestamp`) triggers at most one entry per backtest run.
- Entries are skipped if open simulated positions already equal `config.max_open_trades`.

---

### 5. P&L in R-multiples

**Given** each trade has a defined risk (`|entry_price - stop_loss|`),
**When** a trade is closed,
**Then**:
- `pnl_points = exit_price - entry_price` for BUY; `entry_price - exit_price` for SELL.
- `risk_per_unit = abs(entry_price - stop_loss)` (1R).
- `pnl_r = pnl_points / risk_per_unit` if `risk_per_unit > 0` else `0.0`.
- A full TP hit returns `+2.0R` (given 2:1 RR ratio). A full SL hit returns `-1.0R`.

---

### 6. BacktestResult Metrics

**Given** all closed trades,
**When** `run()` returns,
**Then** `BacktestResult` contains:
- `total_trades: int`
- `win_count: int` — trades with `pnl_r > 0`
- `loss_count: int` — trades with `pnl_r <= 0`
- `win_rate: float` — `win_count / total_trades` (0.0 if no trades)
- `total_r: float` — sum of all `pnl_r`
- `avg_r_per_trade: float` — mean of `pnl_r` (0.0 if no trades)
- `max_drawdown_r: float` — max peak-to-trough of cumulative R curve
- `sharpe_r: float` — `mean(pnl_r) / std(pnl_r)` across trades (0.0 if < 2 trades)
- `profit_factor: float` — `sum(winning R) / abs(sum(losing R))` (0.0 if no losers)
- `trades: List[BacktestTrade]` — all closed trade objects
- `equity_r: List[float]` — cumulative R curve (running total), length = `len(trades)`
- `symbol: str`, `timeframe: str`

---

### 7. Testability

**Given** the backtester operates on DataFrames and pure signal functions,
**When** tested,
**Then** `Backtester.run()` can be called with a synthetic OHLCV DataFrame and mocked signal
functions — no live network or broker required.

---

## Out of Scope (Phase 17)

- Multi-symbol portfolio backtesting (single symbol per `run()` call)
- Slippage modelling (fills assumed at SL/TP/close price exactly)
- Commission/swap costs
- Walk-forward parameter optimisation (Phase 20+)
- HTML/chart output (Phase 20+)
- Partial closes
