# Tasks

## Introduction

Phase 16 — Position Book. Ordered implementation tasks. Read `design.md` fully before starting.
`design.md` contains the complete code for `position_book.py` and the exact three edits needed
in `orchestrator.py`.

> **Two new files. Three edits to one existing file. Nothing else changes.**

---

## Pre-flight

Read these files to confirm APIs before writing any code:

- `python/bridge/feedback_reader.py` — `parse_feedback_file()`, `FeedbackRecord` fields
- `python/risk/manager.py` — `Position` fields (use `stop_loss`/`take_profit`, not `sl`/`tp`)
- `python/core/orchestrator.py` — locate `_update_positions_from_feedback()` stub and `open_positions`

---

## Task 1 — Create `python/core/position_book.py`

Implement exactly as shown in `design.md`. Key rules:

- Import path: `from bridge.feedback_reader import parse_feedback_file, FeedbackRecord`
- Import path: `from risk.manager import Position`
- `update()` uses `glob.glob(os.path.join(self.feedback_folder, "*_result.txt"))`
- `update()` sorts paths for deterministic processing order
- `_apply()` checks `record.status != "FILLED"` first — return early for REJECTED
- `_apply()` for OPEN: `ticket = record.tickets[0] if record.tickets else ""`
- `_apply()` for CLOSE_ALL: filter `self.open_positions` by ticket membership in `set(record.tickets)`
- `parse_feedback_file` failures: `try/except Exception` with `logger.exception`, continue loop

---

## Task 2 — Edit `python/core/orchestrator.py` (three changes)

**Change 1 — add import at the top of the file:**
```python
from core.position_book import PositionBook
```

**Change 2 — add to `__init__`, after the `self.open_positions = []` line:**
```python
self.position_book = PositionBook(config.feedback_folder)
```

**Change 3 — implement `_update_positions_from_feedback()`:**
Replace:
```python
def _update_positions_from_feedback(self) -> None:
    """Stub for Phase 16: position book update from feedback files. No-op."""
    pass
```
With:
```python
def _update_positions_from_feedback(self) -> None:
    """Update open_positions from EA feedback files via PositionBook."""
    new_records = self.position_book.update()
    self.open_positions = self.position_book.open_positions
```

**Change 4 — call the method at the start of `run_cycle()`:**
Add `self._update_positions_from_feedback()` as the **first executable line** inside `run_cycle()`,
before the `t0 = time.monotonic()` line or the symbol loop (whichever comes first).

---

## Task 3 — Create `python/tests/test_position_book.py`

Target: 20-25 tests covering all cases in `design.md`. Use `unittest.mock.patch` to mock
`core.position_book.parse_feedback_file`. For filesystem-scanning tests, use `tmp_path` to
write real `*_result.txt` files.

Minimum test groups:

1. PositionBook defaults (2 tests)
2. `_apply` OPEN — field mapping (6 tests)
3. `_apply` CLOSE_ALL — removal logic (3 tests)
4. `_apply` REJECTED — no-op (2 tests)
5. `update()` deduplication (2 tests)
6. `update()` return value (2 tests)
7. `update()` error handling (1 test)
8. Orchestrator integration (3 tests)

---

## Task 4 — Run position book tests

```bash
cd python
python -m pytest tests/test_position_book.py -v
```

Fix all failures before proceeding.

---

## Task 5 — Full regression

```bash
cd python
python -m pytest -q
```

All existing tests (308+) must still pass. The three edits to `orchestrator.py` must not break any
of the 23 orchestrator tests. If they do, fix the test mocks to account for the new `position_book`
attribute and the `_update_positions_from_feedback()` call at the start of `run_cycle()`.

---

## Task 6 — Commit and push

```bash
git add python/core/position_book.py \
        python/core/orchestrator.py \
        python/tests/test_position_book.py \
        .kiro/specs/position-book/requirements.md \
        .kiro/specs/position-book/design.md \
        .kiro/specs/position-book/tasks.md
git commit -m "feat: Phase 16 complete — position book wired into orchestrator with N tests"
git push origin main
```

---

## Definition of Done

- [ ] `python/core/position_book.py` — `PositionBook` class with `update()` and `_apply()`
- [ ] `python/core/orchestrator.py` — `position_book` in `__init__`, stub implemented, called at start of `run_cycle()`
- [ ] `python/tests/test_position_book.py` — 20+ tests, all passing
- [ ] Full regression (308+ tests) still green
- [ ] Committed and pushed to `origin main`
