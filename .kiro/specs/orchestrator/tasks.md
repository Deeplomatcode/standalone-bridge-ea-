# Tasks

## Introduction

Ordered implementation tasks for Phase 15 — Orchestrator. Build in sequence. Each task depends on
the previous. Read `design.md` fully before starting Task 1 — it contains the complete code for
`config.py` and `orchestrator.py` that you must implement exactly.

> **CRITICAL: Do not modify any existing file.** All work is new files only.

---

## Pre-flight (do before Task 1)

Read these files in full to understand the exact API signatures you will import:

- `python/data/ohlcv.py` — `update_ohlcv()`
- `python/signals/regime.py` — `classify_regime()`, `RegimeLabel`
- `python/signals/order_blocks.py` — `detect_order_blocks()`, `mark_mitigated()`, `OrderBlock`
- `python/signals/strategy.py` — `generate_signals()`, `execute_signals()`, `TradeSignal`
- `python/risk/manager.py` — `RiskManager`, `Position`

---

## Task 1 — Package init

Create `python/core/__init__.py` as an empty file.

---

## Task 2 — TradingConfig

Create `python/core/config.py` exactly as specified in `design.md`.

Verify:
```python
from core.config import TradingConfig
cfg = TradingConfig()
assert cfg.symbols == ["EURUSDm", "XAUUSDm"]
assert cfg.lot_size == 0.01
```

---

## Task 3 — Orchestrator class

Create `python/core/orchestrator.py` exactly as specified in `design.md`.

Rules:
- All imports must use the module paths shown in design.md (`from data.ohlcv import ...` etc.)
- `_run_symbol()` must wrap the entire pipeline in `try/except Exception` with `logger.exception()`
- `_dispatch_signal()` must check `_dispatched_ids` before calling `execute_signals()`
- `run()` must catch `KeyboardInterrupt` and log a clean shutdown message

---

## Task 4 — Unit tests

Create `python/tests/test_orchestrator.py` with pytest. Target: 20-25 tests.

Use `unittest.mock.patch` to mock:
- `core.orchestrator.update_ohlcv`
- `core.orchestrator.classify_regime`
- `core.orchestrator.detect_order_blocks`
- `core.orchestrator.mark_mitigated`
- `core.orchestrator.generate_signals`
- `core.orchestrator.execute_signals`

### Required test cases

**TradingConfig:**
- Default values match the spec (symbols, lot_size, poll_interval, etc.)
- `from_env()` reads `SYMBOLS` env var and splits on comma
- `from_env()` reads `BRIDGE_FOLDER` env var
- `from_env()` falls back to defaults when env vars absent

**Orchestrator initialisation:**
- `RiskManager` is initialised with config values (check `orch.risk_manager.max_open_trades`)

**`run_cycle()` happy path:**
- Returns a dict keyed by symbol
- Each symbol key contains `signals_generated`, `signals_approved`, `signals_dispatched`
- Calls `update_ohlcv` once per symbol
- Calls `generate_signals` once per symbol

**Signal dispatch:**
- Approved signal is dispatched (execute_signals called)
- Rejected signal is NOT dispatched (check `execute_signals` not called for that signal)
- Same signal_id dispatched twice → second call is skipped (dedup)

**Error isolation:**
- When `update_ohlcv` raises for symbol A, symbol B still runs (run_cycle returns both keys)
- Exception does not propagate out of `run_cycle()`

**Multi-symbol:**
- Two symbols → `update_ohlcv` called twice (once per symbol)

### Example test structure

```python
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from core.config import TradingConfig
from core.orchestrator import Orchestrator
from signals.strategy import TradeSignal
from signals.regime import RegimeLabel

def make_df():
    """Minimal OHLCV DataFrame for tests."""
    idx = pd.date_range("2026-01-01", periods=100, freq="h")
    return pd.DataFrame(
        {"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 1000},
        index=idx,
    )

def make_regimes(df):
    return pd.Series([RegimeLabel.TRENDING_UP] * len(df), index=df.index)

def make_signal(symbol="EURUSDm"):
    return TradeSignal(
        symbol=symbol, side="BUY", size=0.01,
        sl=1.09, tp=1.13, signal_id=f"{symbol}_BUY_001"
    )

@patch("core.orchestrator.execute_signals", return_value=["/tmp/test.txt"])
@patch("core.orchestrator.generate_signals")
@patch("core.orchestrator.mark_mitigated", side_effect=lambda obs, df: obs)
@patch("core.orchestrator.detect_order_blocks", return_value=[])
@patch("core.orchestrator.classify_regime")
@patch("core.orchestrator.update_ohlcv")
def test_run_cycle_returns_summary(mock_update, mock_regime, mock_obs, mock_mit, mock_gen, mock_exec):
    df = make_df()
    mock_update.return_value = df
    mock_regime.return_value = make_regimes(df)
    mock_gen.return_value = [make_signal()]

    cfg = TradingConfig(symbols=["EURUSDm"], lot_size=0.01)
    orch = Orchestrator(cfg)
    summary = orch.run_cycle()

    assert "EURUSDm" in summary
    assert summary["EURUSDm"]["signals_generated"] == 1
```

---

## Task 5 — Run tests

```bash
cd python
python -m pytest tests/test_orchestrator.py -v
```

Fix all failures before proceeding.

---

## Task 6 — Full regression

```bash
cd python
python -m pytest -q
```

All 285+ existing tests must still pass. If any fail, fix before committing.

---

## Task 7 — Commit and push

```bash
cd "Standalone Bridge EA Design"
git add python/core/__init__.py python/core/config.py python/core/orchestrator.py \
        python/tests/test_orchestrator.py \
        .kiro/specs/orchestrator/requirements.md \
        .kiro/specs/orchestrator/design.md \
        .kiro/specs/orchestrator/tasks.md
git commit -m "feat: Phase 15 complete — orchestrator + TradingConfig with N tests"
git push origin main
```

Replace N with the actual number of orchestrator tests.

---

## Definition of Done

- [ ] `python/core/__init__.py` exists (empty)
- [ ] `python/core/config.py` — `TradingConfig` with `from_env()`, all 17 fields, correct defaults
- [ ] `python/core/orchestrator.py` — `Orchestrator` class with `run()`, `run_cycle()`, `_run_symbol()`, `_dispatch_signal()`
- [ ] `python/tests/test_orchestrator.py` — 20+ tests, all passing
- [ ] Full test suite (285+ tests) still green
- [ ] Committed and pushed to `origin main`
