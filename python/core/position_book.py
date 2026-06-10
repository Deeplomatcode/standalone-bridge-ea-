"""
python/core/position_book.py

Live position registry — Phase 16.

Maintains the list of open positions by scanning EA feedback files.
Provides the open_positions list that the risk manager uses to gate new signals.
"""

from __future__ import annotations

import glob
import logging
import os
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
