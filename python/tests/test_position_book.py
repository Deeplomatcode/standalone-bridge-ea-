"""
python/tests/test_position_book.py

Unit tests for the position book (Phase 16).
parse_feedback_file is mocked — no real file reads.
"""

import os
from unittest.mock import patch, MagicMock
from dataclasses import field

import pytest

from core.position_book import PositionBook
from bridge.feedback_reader import FeedbackRecord
from risk.manager import Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_PARSE = "core.position_book.parse_feedback_file"


def make_open_record(id="test_001", asset="EURUSD", side="BUY", size=0.01,
                     avg_price=1.1050, tickets=None):
    """Create a FILLED OPEN feedback record."""
    return FeedbackRecord(
        id=id,
        status="FILLED",
        asset=asset,
        action="OPEN",
        side=side,
        size=size,
        tickets=tickets if tickets is not None else ["12345"],
        avg_price=avg_price,
        message="",
        error_code=0,
    )


def make_close_record(id="close_001", asset="EURUSD", tickets=None):
    """Create a FILLED CLOSE_ALL feedback record."""
    return FeedbackRecord(
        id=id,
        status="FILLED",
        asset=asset,
        action="CLOSE_ALL",
        side="",
        size=0.0,
        tickets=tickets or ["12345"],
        avg_price=0.0,
        message="",
        error_code=0,
    )


def make_rejected_record(id="rej_001", action="OPEN"):
    """Create a REJECTED feedback record."""
    return FeedbackRecord(
        id=id,
        status="REJECTED",
        asset="EURUSD",
        action=action,
        side="BUY",
        size=0.01,
        tickets=[],
        avg_price=0.0,
        message="LotSizeExceeded",
        error_code=1,
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestPositionBookDefaults:

    def test_open_positions_starts_empty(self):
        pb = PositionBook("some/folder")
        assert pb.open_positions == []

    def test_processed_ids_starts_empty(self):
        pb = PositionBook("some/folder")
        assert len(pb._processed_ids) == 0


# ---------------------------------------------------------------------------
# _apply() — OPEN
# ---------------------------------------------------------------------------

class TestApplyOpen:

    def test_filled_open_adds_position(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record())
        assert len(pb.open_positions) == 1

    def test_position_symbol_from_asset(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(asset="XAUUSD"))
        assert pb.open_positions[0].symbol == "XAUUSD"

    def test_position_side_from_record(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(side="SELL"))
        assert pb.open_positions[0].side == "SELL"

    def test_position_size_from_record(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(size=0.05))
        assert pb.open_positions[0].size == 0.05

    def test_position_entry_price_from_avg_price(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(avg_price=2380.50))
        assert pb.open_positions[0].entry_price == 2380.50

    def test_position_ticket_from_tickets_list(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(tickets=["99999"]))
        assert pb.open_positions[0].ticket == "99999"

    def test_position_ticket_empty_when_no_tickets(self):
        pb = PositionBook("some/folder")
        pb._apply(make_open_record(tickets=[]))
        assert pb.open_positions[0].ticket == ""


# ---------------------------------------------------------------------------
# _apply() — CLOSE_ALL
# ---------------------------------------------------------------------------

class TestApplyCloseAll:

    def test_removes_matching_ticket(self):
        pb = PositionBook("some/folder")
        pb.open_positions = [
            Position(symbol="EURUSD", side="BUY", size=0.01, ticket="111"),
            Position(symbol="EURUSD", side="SELL", size=0.01, ticket="222"),
        ]
        pb._apply(make_close_record(tickets=["111"]))
        assert len(pb.open_positions) == 1
        assert pb.open_positions[0].ticket == "222"

    def test_non_matching_tickets_remain(self):
        pb = PositionBook("some/folder")
        pb.open_positions = [
            Position(symbol="EURUSD", side="BUY", size=0.01, ticket="333"),
        ]
        pb._apply(make_close_record(tickets=["999"]))
        assert len(pb.open_positions) == 1

    def test_empty_ticket_positions_not_removed(self):
        pb = PositionBook("some/folder")
        pb.open_positions = [
            Position(symbol="EURUSD", side="BUY", size=0.01, ticket=""),
        ]
        pb._apply(make_close_record(tickets=["111"]))
        assert len(pb.open_positions) == 1


# ---------------------------------------------------------------------------
# _apply() — REJECTED
# ---------------------------------------------------------------------------

class TestApplyRejected:

    def test_rejected_open_does_not_add(self):
        pb = PositionBook("some/folder")
        pb._apply(make_rejected_record(action="OPEN"))
        assert len(pb.open_positions) == 0

    def test_rejected_close_does_not_remove(self):
        pb = PositionBook("some/folder")
        pb.open_positions = [
            Position(symbol="EURUSD", side="BUY", size=0.01, ticket="111"),
        ]
        pb._apply(make_rejected_record(action="CLOSE_ALL"))
        assert len(pb.open_positions) == 1


# ---------------------------------------------------------------------------
# update() — deduplication
# ---------------------------------------------------------------------------

class TestUpdateDedup:

    @patch(PATCH_PARSE)
    @patch("core.position_book.glob.glob")
    def test_same_id_applied_only_once(self, mock_glob, mock_parse):
        mock_glob.return_value = ["f1.txt", "f2.txt"]
        # Both files parse to the same record ID
        mock_parse.return_value = make_open_record(id="dup_001")

        pb = PositionBook("some/folder")
        pb.update()
        assert len(pb.open_positions) == 1

    @patch(PATCH_PARSE)
    @patch("core.position_book.glob.glob")
    def test_second_call_skips_already_processed(self, mock_glob, mock_parse):
        mock_glob.return_value = ["f1.txt"]
        mock_parse.return_value = make_open_record(id="dup_002")

        pb = PositionBook("some/folder")
        pb.update()
        pb.update()  # same file found again
        assert len(pb.open_positions) == 1


# ---------------------------------------------------------------------------
# update() — return value
# ---------------------------------------------------------------------------

class TestUpdateReturnValue:

    @patch(PATCH_PARSE)
    @patch("core.position_book.glob.glob")
    def test_returns_new_records(self, mock_glob, mock_parse):
        mock_glob.return_value = ["f1.txt"]
        rec = make_open_record(id="new_001")
        mock_parse.return_value = rec

        pb = PositionBook("some/folder")
        result = pb.update()
        assert len(result) == 1
        assert result[0].id == "new_001"

    @patch("core.position_book.glob.glob", return_value=[])
    def test_returns_empty_when_no_files(self, mock_glob):
        pb = PositionBook("some/folder")
        result = pb.update()
        assert result == []


# ---------------------------------------------------------------------------
# update() — error handling
# ---------------------------------------------------------------------------

class TestUpdateErrorHandling:

    @patch(PATCH_PARSE)
    @patch("core.position_book.glob.glob")
    def test_bad_file_skipped_others_processed(self, mock_glob, mock_parse):
        mock_glob.return_value = ["bad.txt", "good.txt"]

        def side_effect(path):
            if path == "bad.txt":
                raise ValueError("corrupt file")
            return make_open_record(id="good_001")

        mock_parse.side_effect = side_effect

        pb = PositionBook("some/folder")
        result = pb.update()
        assert len(result) == 1
        assert result[0].id == "good_001"
        assert len(pb.open_positions) == 1


# ---------------------------------------------------------------------------
# Orchestrator integration
# ---------------------------------------------------------------------------

class TestOrchestratorIntegration:

    def test_orchestrator_has_position_book(self):
        from core.config import TradingConfig
        from core.orchestrator import Orchestrator
        cfg = TradingConfig()
        orch = Orchestrator(cfg)
        assert hasattr(orch, "position_book")
        assert isinstance(orch.position_book, PositionBook)

    @patch("core.orchestrator.update_ohlcv")
    @patch("core.orchestrator.classify_regime")
    @patch("core.orchestrator.detect_order_blocks", return_value=[])
    @patch("core.orchestrator.mark_mitigated", side_effect=lambda obs, df: obs)
    @patch("core.orchestrator.generate_signals", return_value=[])
    @patch("core.orchestrator.execute_signals", return_value=[])
    def test_run_cycle_calls_update_positions(self, *mocks):
        import pandas as pd
        from core.config import TradingConfig
        from core.orchestrator import Orchestrator
        from signals.regime import RegimeLabel

        df = pd.DataFrame(
            {"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 1000.0},
            index=pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC"),
        )
        # Mock update_ohlcv and classify_regime
        mocks[5].return_value = df  # update_ohlcv
        mocks[4].return_value = pd.Series([RegimeLabel.TRENDING_UP] * 100, index=df.index)

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)

        with patch.object(orch.position_book, "update", return_value=[]) as mock_pb_update:
            orch.run_cycle()
            mock_pb_update.assert_called_once()

    @patch(PATCH_PARSE)
    @patch("core.position_book.glob.glob")
    def test_open_positions_synced_after_update(self, mock_glob, mock_parse):
        from core.config import TradingConfig
        from core.orchestrator import Orchestrator

        mock_glob.return_value = ["f1.txt"]
        mock_parse.return_value = make_open_record(id="sync_001")

        cfg = TradingConfig()
        orch = Orchestrator(cfg)
        orch._update_positions_from_feedback()
        assert len(orch.open_positions) == 1
        assert orch.open_positions[0].symbol == "EURUSD"
