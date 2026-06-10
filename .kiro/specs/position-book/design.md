# Design

## Introduction

Phase 16 creates one new file (`position_book.py`) and makes two targeted edits to
`orchestrator.py`. Nothing else changes.

---

## Files

```
python/core/position_book.py          ← NEW — PositionBook class
python/core/orchestrator.py           ← EDIT — implement stub + add position_book init
python/tests/test_position_book.py    ← NEW — pytest tests (20-25)
```

---

## Verified API Reference

Read these before writing any code — field names confirmed from actual source files.

### `python/bridge/feedback_reader.py`
```python
@dataclass
class FeedbackRecord:
    id: str          = ""
    status: str      = ""      # "FILLED" or "REJECTED"
    asset: str       = ""
    action: str      = ""      # "OPEN" or "CLOSE_ALL"
    side: str        = ""      # "BUY", "SELL", or "" for CLOSE_ALL
    size: float      = 0.0
    tickets: List[str] = field(default_factory=list)
    avg_price: float = 0.0
    message: str     = ""
    error_code: int  = 0

    @property
    def is_filled(self) -> bool: ...
    @property
    def is_rejected(self) -> bool: ...

def parse_feedback_file(path: str) -> FeedbackRecord: ...
```

### `python/risk/manager.py`
```python
@dataclass
class Position:
    symbol:      str
    side:        str
    size:        float
    entry_price: float = 0.0
    stop_loss:   float = 0.0
    take_profit: float = 0.0
    ticket:      str   = ""
```

### `python/core/orchestrator.py` — stub to replace
```python
def _update_positions_from_feedback(self) -> None:
    """Stub for Phase 16: position book update from feedback files. No-op."""
    pass
```

---

## `python/core/position_book.py` — Complete Implementation

```python
from __future__ import annotations
import glob
import logging
import os
from dataclasses import dataclass, field
from typing import List, Set

from bridge.feedback_reader import parse_feedback_file, FeedbackRecord
from risk.manager import Position

logger = logging.getLogger(__name__)


class PositionBook:
    """Live registry of open positions, built from EA feedback files.

    Call update() at the start of each orchestrator cycle to process new
    feedback files and keep open_positions current.
    """

    def __init__(self, feedback_folder: str) -> None:
        self.feedback_folder: str = feedback_folder
        self.open_positions: List[Position] = []
        self._processed_ids: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self) -> List[FeedbackRecord]:
        """Scan feedback_folder for new *_result.txt files and apply them.

        Returns:
            List of FeedbackRecord objects processed in this call.
            Returns an empty list if no new files were found.
        """
        pattern = os.path.join(self.feedback_folder, "*_result.txt")
        paths = glob.glob(pattern)

        new_records: List[FeedbackRecord] = []
        for path in sorted(paths):            # sorted for deterministic order in tests
            try:
                record = parse_feedback_file(path)
            except Exception:
                logger.exception(f"Failed to parse feedback file: {path}")
                continue

            if record.id in self._processed_ids:
                continue                      # already applied — skip silently

            self._processed_ids.add(record.id)
            self._apply(record)
            new_records.append(record)
            logger.debug(f"Position book: applied {record.id} ({record.status} {record.action})")

        if new_records:
            logger.info(
                f"Position book: {len(new_records)} new record(s), "
                f"{len(self.open_positions)} open position(s)"
            )

        return new_records

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply(self, record: FeedbackRecord) -> None:
        """Update open_positions based on a single feedback record."""
        if record.status != "FILLED":
            return                            # REJECTED — no position change

        if record.action == "OPEN":
            pos = Position(
                symbol=record.asset,
                side=record.side,
                size=record.size,
                entry_price=record.avg_price,
                stop_loss=0.0,
                take_profit=0.0,
                ticket=record.tickets[0] if record.tickets else "",
            )
            self.open_positions.append(pos)
            logger.info(
                f"Position OPENED: {pos.symbol} {pos.side} {pos.size}L "
                f"@ {pos.entry_price}  ticket={pos.ticket}"
            )

        elif record.action == "CLOSE_ALL":
            closed_tickets = set(record.tickets)
            before = len(self.open_positions)
            self.open_positions = [
                p for p in self.open_positions
                if p.ticket not in closed_tickets
            ]
            removed = before - len(self.open_positions)
            logger.info(
                f"CLOSE_ALL: removed {removed} position(s) for tickets={record.tickets}"
            )
```

---

## Orchestrator edits — two changes only

### Change 1: Add `position_book` to `__init__`

Add after `self.open_positions = []`:

```python
from core.position_book import PositionBook   # add to imports at top of file

# inside __init__, after self.open_positions line:
self.position_book = PositionBook(config.feedback_folder)
```

### Change 2: Implement `_update_positions_from_feedback()`

Replace the no-op stub with:

```python
def _update_positions_from_feedback(self) -> None:
    """Update open_positions from EA feedback files via PositionBook."""
    new_records = self.position_book.update()
    self.open_positions = self.position_book.open_positions
```

### Change 3: Call the method at the start of `run_cycle()`

Inside `run_cycle()`, add this as the **first line** inside the method body (before the symbol loop):

```python
self._update_positions_from_feedback()
```

> The method already exists — just add the call. The logger.info line at the start of run_cycle and the symbol loop follow after.

---

## Tests — `python/tests/test_position_book.py`

Mock `parse_feedback_file` with `unittest.mock.patch`. Use `tmp_path` (pytest fixture) to create
real `*_result.txt` files on disk where real file scanning is needed.

### Required test cases (aim for 20-25)

**Defaults:**
- `open_positions` starts empty
- `_processed_ids` starts empty

**`_apply()` — OPEN:**
- FILLED + OPEN adds one Position to open_positions
- Position.symbol = record.asset
- Position.side = record.side
- Position.size = record.size
- Position.entry_price = record.avg_price
- Position.ticket = record.tickets[0] when tickets non-empty
- Position.ticket = "" when tickets is empty list

**`_apply()` — CLOSE_ALL:**
- FILLED + CLOSE_ALL removes positions matching tickets
- Positions with non-matching tickets remain
- Positions with ticket="" are not removed by CLOSE_ALL

**`_apply()` — REJECTED:**
- REJECTED + OPEN does NOT add a position
- REJECTED + CLOSE_ALL does NOT remove positions

**`update()` deduplication:**
- Same record ID processed twice → applied only once (open_positions has 1 item, not 2)

**`update()` return value:**
- Returns list of FeedbackRecord objects processed this call
- Returns empty list when no new files

**`update()` error handling:**
- `parse_feedback_file` raises exception → file skipped, other files still processed

**Orchestrator integration:**
- `Orchestrator.__init__` creates `position_book` attribute
- `run_cycle()` calls `_update_positions_from_feedback()` (verify via mock)
- After update, `orchestrator.open_positions` reflects `position_book.open_positions`

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `parse_feedback_file` raises | `logger.exception`, file skipped, loop continues |
| `feedback_folder` does not exist | `glob.glob` returns `[]`, `update()` returns `[]` — no crash |
| Duplicate record ID | Silently skipped — not applied a second time |
| CLOSE_ALL with ticket not in open_positions | No-op (list comprehension handles gracefully) |
