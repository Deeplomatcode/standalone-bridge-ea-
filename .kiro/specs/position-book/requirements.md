# Requirements

## Introduction

The position book (`python/core/position_book.py`) maintains the live list of open positions by
reading EA feedback files from `FeedbackFolder`. It provides the `open_positions` list that the
risk manager uses to gate new signals. Without it, the risk manager always sees an empty book and
cannot enforce per-symbol or total lot limits correctly.

Phase 16 also wires the `PositionBook` into the existing `Orchestrator` by implementing the
`_update_positions_from_feedback()` stub that was left empty in Phase 15.

---

## Requirements

### 1. Scan Feedback Folder

**Given** the EA writes feedback files to `FeedbackFolder` after every action,
**When** `position_book.update()` is called,
**Then** it scans for all `*_result.txt` files in `feedback_folder` and processes any files whose
`id` has not already been processed this session.

### 2. Apply FILLED OPEN — Add Position

**Given** a feedback file has `status=FILLED` and `action=OPEN`,
**When** `update()` processes that record,
**Then** it creates a new `Position` object with:
- `symbol` = `record.asset`
- `side` = `record.side`
- `size` = `record.size`
- `entry_price` = `record.avg_price`
- `ticket` = `record.tickets[0]` if tickets list is non-empty, else `""`
- `stop_loss` = 0.0 (not in feedback — set to neutral)
- `take_profit` = 0.0 (not in feedback — set to neutral)

And appends it to `open_positions`.

### 3. Apply FILLED CLOSE_ALL — Remove Positions

**Given** a feedback file has `status=FILLED` and `action=CLOSE_ALL`,
**When** `update()` processes that record,
**Then** all positions whose `ticket` matches any ticket in `record.tickets` are removed from
`open_positions`. Positions with no ticket (`ticket == ""`) are left untouched.

### 4. Ignore REJECTED Records

**Given** a feedback file has `status=REJECTED`,
**When** `update()` processes that record,
**Then** `open_positions` is not modified — rejected actions produced no positions.

### 5. Deduplication

**Given** `update()` is called repeatedly in a loop,
**When** a feedback file has already been processed in a previous call,
**Then** it is silently skipped — the same record is never applied twice.

### 6. Return New Records

**Given** `update()` processes N new feedback files,
**When** it returns,
**Then** it returns a list of the `FeedbackRecord` objects that were processed in this call (empty list if none new).

### 7. Orchestrator Integration

**Given** `Orchestrator._update_positions_from_feedback()` is a no-op stub,
**When** Phase 16 is complete,
**Then** the stub is implemented to:
1. Call `self.position_book.update()`
2. Log the count of new records and current open position count
3. `self.open_positions` on `Orchestrator` must stay in sync — it should reference `self.position_book.open_positions`

And `run_cycle()` calls `self._update_positions_from_feedback()` at the **start** of each cycle, before the symbol loop.

---

## Out of Scope (Phase 16)

- Partial close tracking (Phase 20+)
- Equity / drawdown polling (requires broker API)
- Persistence across process restarts (positions are rebuilt from feedback files on startup)
- Stop-loss / take-profit tracking in the position book (not in feedback protocol)
