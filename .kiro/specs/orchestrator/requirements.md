# Requirements

## Introduction

The orchestrator (`python/core/orchestrator.py`) is the main run loop that ties all existing Python
modules into a continuously running trading system. It fetches data, classifies regime, detects order
blocks, generates signals, gates them through the risk manager, and dispatches approved signals to the
EA bridge. It runs in a configurable polling loop on the host machine — the same Windows box running MT4.

## Requirements

---

### 1. Configuration Loading

**Given** the operator wants to control all runtime parameters in one place,
**When** the orchestrator starts,
**Then** it reads all configuration from a `TradingConfig` dataclass that can be populated from
environment variables (via `TradingConfig.from_env()`) with sensible defaults for every field.

#### Fields required in TradingConfig
- `symbols: List[str]` — broker symbols to trade (e.g. `["EURUSDm", "XAUUSDm"]`)
- `timeframe: str` — OHLCV timeframe string (e.g. `"H1"`)
- `bridge_folder: str` — EA `BridgeFolder` path (env: `BRIDGE_FOLDER`, default: `"bridge/outgoing"`)
- `feedback_folder: str` — EA `FeedbackFolder` path (env: `FEEDBACK_FOLDER`, default: `"bridge/incoming"`)
- `data_dir: str` — OHLCV CSV storage directory (env: `DATA_DIR`, default: `"data/csv"`)
- `poll_interval: int` — seconds between run cycles (default: 60)
- `feedback_timeout: int` — seconds to wait for EA feedback per signal (default: 90)
- `lookback_days: int` — days of OHLCV history to fetch/update (default: 30)
- `max_open_trades: int` — risk gate parameter (default: 5)
- `max_lot_per_symbol: float` — risk gate parameter (default: 0.10)
- `max_lot_total: float` — risk gate parameter (default: 0.50)
- `max_drawdown_pct: float` — risk gate parameter (default: 10.0)
- `lot_size: float` — default lot size for all generated signals (default: 0.01)
- `sl_buffer: float` — OB stop-loss buffer passed to generate_signals (default: 0.0002)
- `rr_ratio: float` — reward-to-risk ratio passed to generate_signals (default: 2.0)
- `adx_trend_threshold: float` — ADX threshold passed to classify_regime (default: 25.0)

---

### 2. Per-Symbol Run Cycle

**Given** the operator has configured one or more symbols,
**When** a run cycle executes,
**Then** for each symbol the orchestrator:
1. Calls `update_ohlcv(data_dir, symbol, timeframe, lookback_days)` to get a fresh DataFrame
2. Calls `classify_regime(df, adx_trend_threshold=...)` to get a `pd.Series` of regime labels
3. Calls `detect_order_blocks(df)` to get a list of `OrderBlock` objects
4. Calls `mark_mitigated(obs, df)` to mark stale OBs as inactive
5. Calls `generate_signals(df, symbol, obs, regimes, size=lot_size, sl_buffer=..., rr_ratio=...)` to get `TradeSignal` objects
6. For each signal, calls `risk_manager.check_signal(signal, open_positions)` — dispatches only if approved

---

### 3. Signal Deduplication

**Given** the orchestrator runs repeatedly on the same OHLCV data,
**When** a signal with an ID that has already been dispatched this session is encountered,
**Then** it is silently skipped — the same signal is never dispatched twice in one session.

---

### 4. Risk Gate Integration

**Given** the risk manager enforces position limits,
**When** a signal passes all checks in `risk_manager.check_signal()`,
**Then** it is dispatched to the EA bridge via `execute_signals([signal], bridge_folder)`.
**When** a signal is rejected by the risk manager,
**Then** it is logged with the rejection reason and skipped.

---

### 5. Error Isolation Per Symbol

**Given** a network error, data gap, or unexpected exception occurs for one symbol,
**When** `run_cycle()` is processing that symbol,
**Then** the error is caught, logged with the symbol name and traceback, and processing continues
for all other symbols — one broken symbol must never crash the entire loop.

---

### 6. Continuous Run Loop

**Given** the operator starts the orchestrator,
**When** `run()` is called,
**Then** it runs `run_cycle()` continuously, sleeping `poll_interval` seconds between cycles,
until interrupted by `KeyboardInterrupt`, which it catches and logs as a clean shutdown.

---

### 7. Logging

**Given** the operator needs observability,
**When** each cycle runs,
**Then** the orchestrator logs:
- Startup configuration summary
- Each cycle start with timestamp
- Per-symbol: regime, OB count, signal count, approved count
- Each dispatched signal (symbol, side, size, signal_id)
- Each rejected signal with reason
- Each exception with symbol and traceback
- Cycle completion with elapsed time
- Clean shutdown message

---

### 8. Testability

**Given** the orchestrator has a `run_cycle()` method,
**When** tests call it with mocked module functions,
**Then** it returns a dict with per-symbol summary data that tests can assert against.
`run_cycle()` must be callable in isolation without starting the loop.

---

## Out of Scope (Phase 15)

- Position book (Phase 16) — `open_positions` is always `[]` in this phase
- Feedback polling in the run loop (Phase 16) — dispatched and forgotten
- Live equity/drawdown check — requires position book
- Session/killzone filter (Phase 18)
- ATR-based lot sizing (Phase 19)
- Dashboard or web UI (Global System)
