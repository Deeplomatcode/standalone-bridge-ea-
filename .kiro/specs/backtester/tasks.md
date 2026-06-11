# Tasks

## Introduction

Phase 17 — Backtester. Two new files only. Read `design.md` fully — it contains the complete
implementation of `backtester.py`. Follow tasks in order.

> **Do not modify any existing file.**

---

## Pre-flight

Read these files to confirm APIs:

- `python/signals/strategy.py` — `TradeSignal` fields: `stop_loss`, `take_profit`, `entry_price`, `timestamp`, `ob`, `side`, `symbol`, `size`, `regime`
- `python/signals/regime.py` — `classify_regime()` signature
- `python/signals/order_blocks.py` — `detect_order_blocks()`, `mark_mitigated()`, `OrderBlock.timestamp`
- `python/core/config.py` — `TradingConfig` fields used: `lot_size`, `sl_buffer`, `rr_ratio`, `adx_trend_threshold`, `max_open_trades`, `timeframe`

---

## Task 1 — Create `python/core/backtester.py`

Implement exactly as in `design.md`. Key rules:

- Three classes: `BacktestTrade`, `BacktestResult`, `Backtester`
- `BacktestTrade` and `BacktestResult` are `@dataclass` with all fields from the design
- `Backtester.__init__` takes `config: TradingConfig` and `warmup_bars: int = 60`
- `run()` pre-computes `all_regimes` and `all_obs` on the full df, then iterates bar by bar
- OB filter in bar loop: `[ob for ob in all_obs if ob.timestamp < ts]` (no lookahead)
- SL check before TP check — SL wins when both hit same bar
- `_close_trade()` computes `pnl_points` and `pnl_r` — use `risk = abs(entry - stop_loss)`
- `_compute_result()` computes all 8 metrics from the closed trade list
- `_empty_result()` returns `BacktestResult` with all counts/rates as 0

Import paths:
```python
from core.config import TradingConfig
from signals.regime import classify_regime
from signals.order_blocks import detect_order_blocks, mark_mitigated, OrderBlock
from signals.strategy import generate_signals
```

No new packages — uses only `math`, `logging`, `dataclasses`, `typing`, `pandas`.

---

## Task 2 — Create `python/tests/test_backtester.py`

Target: 20-25 tests. Use `unittest.mock.patch` for integration-level tests where needed.
Use the `make_trending_df()` and `make_flat_df()` helpers from `design.md`.

Minimum test groups (from `design.md`):
1. `BacktestTrade` defaults (2 tests)
2. `BacktestResult` — `summary()` and empty result (2 tests)
3. `_close_trade()` BUY — pnl_points, pnl_r at TP, pnl_r at SL, exit_reason (4 tests)
4. `_close_trade()` SELL — pnl direction (2 tests)
5. `_check_exit()` BUY — SL hit, TP hit, both hit (SL wins), neither (4 tests)
6. `_check_exit()` SELL — SL hit, TP hit, neither (3 tests)
7. `_compute_result()` — win_rate, total_r, equity_r length, drawdown, profit_factor, sharpe (6 tests)
8. `run()` integration — returns result, short df → empty, EOD closure, OB dedup (4 tests)

---

## Task 3 — Run backtester tests

```bash
cd python
python -m pytest tests/test_backtester.py -v
```

Fix all failures.

---

## Task 4 — Full regression

```bash
cd python
python -m pytest -q
```

330+ tests must pass. The backtester tests add to the suite; existing tests must remain green.

---

## Task 5 — Commit and push

```bash
git add python/core/backtester.py \
        python/tests/test_backtester.py \
        .kiro/specs/backtester/requirements.md \
        .kiro/specs/backtester/design.md \
        .kiro/specs/backtester/tasks.md
git commit -m "feat: Phase 17 complete — backtester with bar-by-bar simulation and N tests"
git push origin main
```

---

## Definition of Done

- [ ] `python/core/backtester.py` — `BacktestTrade`, `BacktestResult`, `Backtester` with all methods
- [ ] `python/tests/test_backtester.py` — 20+ tests, all passing
- [ ] Full regression (330+ tests) green
- [ ] Committed and pushed to `origin main`
