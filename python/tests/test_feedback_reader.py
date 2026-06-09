"""
python/tests/test_feedback_reader.py

Unit tests for bridge/feedback_reader.py.

Tests cover:
  - FeedbackRecord dataclass defaults and properties
  - parse_feedback_file: FILLED OPEN, FILLED CLOSE_ALL (multiple tickets),
    REJECTED, missing fields, blank fields, bad float/int, empty file,
    file not found
  - poll_for_feedback: finds file before timeout, times out, file appears
    mid-poll
"""

import os
import time
import threading
import pytest

from bridge.feedback_reader import (
    FeedbackRecord,
    parse_feedback_file,
    poll_for_feedback,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_feedback(path: str, **kwargs) -> None:
    """Write a key=value feedback file for testing."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for key, value in kwargs.items():
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# FeedbackRecord — defaults and properties
# ---------------------------------------------------------------------------

class TestFeedbackRecordDefaults:

    def test_default_status_empty(self):
        r = FeedbackRecord()
        assert r.status == ""

    def test_default_tickets_empty_list(self):
        r = FeedbackRecord()
        assert r.tickets == []

    def test_default_error_code_zero(self):
        r = FeedbackRecord()
        assert r.error_code == 0

    def test_is_filled_true(self):
        r = FeedbackRecord(status="FILLED")
        assert r.is_filled is True
        assert r.is_rejected is False
        assert r.is_error is False

    def test_is_rejected_true(self):
        r = FeedbackRecord(status="REJECTED")
        assert r.is_rejected is True
        assert r.is_filled is False
        assert r.is_error is False

    def test_is_error_true(self):
        r = FeedbackRecord(status="ERROR")
        assert r.is_error is True
        assert r.is_filled is False
        assert r.is_rejected is False

    def test_unknown_status_all_false(self):
        r = FeedbackRecord(status="PENDING")
        assert r.is_filled is False
        assert r.is_rejected is False
        assert r.is_error is False


# ---------------------------------------------------------------------------
# parse_feedback_file — FILLED OPEN
# ---------------------------------------------------------------------------

class TestParseFeedbackFilledOpen:

    def test_parses_all_fields(self, tmp_path):
        path = str(tmp_path / "EURUSD_20260523_142301_a3f8c1d2_result.txt")
        write_feedback(
            path,
            id="EURUSD_20260523_142301_a3f8c1d2",
            status="FILLED",
            asset="EURUSDm",
            action="OPEN",
            side="BUY",
            size="0.10",
            tickets="371932593",
            avg_price="1.15382",
            message="",
            error_code="0",
        )
        r = parse_feedback_file(path)
        assert r.id         == "EURUSD_20260523_142301_a3f8c1d2"
        assert r.status     == "FILLED"
        assert r.asset      == "EURUSDm"
        assert r.action     == "OPEN"
        assert r.side       == "BUY"
        assert r.size       == pytest.approx(0.10)
        assert r.tickets    == ["371932593"]
        assert r.avg_price  == pytest.approx(1.15382)
        assert r.message    == ""
        assert r.error_code == 0

    def test_is_filled_true(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", error_code="0")
        assert parse_feedback_file(path).is_filled is True

    def test_sell_side_preserved(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", side="SELL", error_code="0")
        assert parse_feedback_file(path).side == "SELL"


# ---------------------------------------------------------------------------
# parse_feedback_file — FILLED CLOSE_ALL (multiple tickets)
# ---------------------------------------------------------------------------

class TestParseFeedbackFilledCloseAll:

    def test_parses_multiple_tickets(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(
            path,
            id="EURUSD_20260523_142302_b3f9c2d3",
            status="FILLED",
            asset="EURUSDm",
            action="CLOSE_ALL",
            side="",
            size="0.00",
            tickets="371932593,371929691",
            avg_price="0.00000",
            message="",
            error_code="0",
        )
        r = parse_feedback_file(path)
        assert r.action  == "CLOSE_ALL"
        assert r.tickets == ["371932593", "371929691"]
        assert r.side    == ""
        assert r.size    == pytest.approx(0.0)

    def test_three_tickets(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", tickets="111,222,333", error_code="0")
        assert parse_feedback_file(path).tickets == ["111", "222", "333"]

    def test_tickets_with_spaces(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", tickets="111, 222 , 333", error_code="0")
        assert parse_feedback_file(path).tickets == ["111", "222", "333"]


# ---------------------------------------------------------------------------
# parse_feedback_file — REJECTED
# ---------------------------------------------------------------------------

class TestParseFeedbackRejected:

    def test_rejected_status(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(
            path,
            status="REJECTED",
            asset="EURUSDm",
            message="132",
            error_code="2",
        )
        r = parse_feedback_file(path)
        assert r.is_rejected is True
        assert r.error_code  == 2
        assert r.message     == "132"

    def test_rejected_empty_tickets(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="REJECTED", tickets="", error_code="1")
        assert parse_feedback_file(path).tickets == []


# ---------------------------------------------------------------------------
# parse_feedback_file — missing / blank / malformed fields
# ---------------------------------------------------------------------------

class TestParseFeedbackEdgeCases:

    def test_missing_optional_fields_use_defaults(self, tmp_path):
        path = str(tmp_path / "r.txt")
        # Only write status — all other fields absent
        write_feedback(path, status="FILLED")
        r = parse_feedback_file(path)
        assert r.id         == ""
        assert r.asset      == ""
        assert r.side       == ""
        assert r.size       == 0.0
        assert r.tickets    == []
        assert r.avg_price  == 0.0
        assert r.message    == ""
        assert r.error_code == 0

    def test_blank_size_defaults_to_zero(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", size="")
        assert parse_feedback_file(path).size == 0.0

    def test_bad_float_size_defaults_to_zero(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", size="not_a_number")
        assert parse_feedback_file(path).size == 0.0

    def test_bad_int_error_code_defaults_to_zero(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", error_code="bad")
        assert parse_feedback_file(path).error_code == 0

    def test_blank_tickets_returns_empty_list(self, tmp_path):
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", tickets="")
        assert parse_feedback_file(path).tickets == []

    def test_value_with_equals_sign(self, tmp_path):
        """Values containing '=' must be preserved (partition on first '=' only)."""
        path = str(tmp_path / "r.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("message=some=weird=message\n")
            f.write("status=REJECTED\n")
        r = parse_feedback_file(path)
        assert r.message == "some=weird=message"

    def test_lines_without_equals_skipped(self, tmp_path):
        path = str(tmp_path / "r.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("this line has no equals\n")
            f.write("status=FILLED\n")
        assert parse_feedback_file(path).status == "FILLED"

    def test_empty_file_raises_value_error(self, tmp_path):
        path = str(tmp_path / "r.txt")
        open(path, "w").close()
        with pytest.raises(ValueError, match="no key=value"):
            parse_feedback_file(path)

    def test_missing_file_raises_ioerror(self, tmp_path):
        path = str(tmp_path / "nonexistent_result.txt")
        with pytest.raises(IOError):
            parse_feedback_file(path)

    def test_asset_casing_preserved(self, tmp_path):
        """Asset field must preserve broker casing (EURUSDm not EURUSDM)."""
        path = str(tmp_path / "r.txt")
        write_feedback(path, status="FILLED", asset="EURUSDm")
        assert parse_feedback_file(path).asset == "EURUSDm"


# ---------------------------------------------------------------------------
# poll_for_feedback
# ---------------------------------------------------------------------------

class TestPollForFeedback:

    def test_finds_file_immediately(self, tmp_path):
        action_id = "EURUSD_20260523_142301_a3f8c1d2"
        path = str(tmp_path / f"{action_id}_result.txt")
        write_feedback(path, status="FILLED", error_code="0")
        result = poll_for_feedback(str(tmp_path), action_id, timeout=5.0)
        assert result is not None
        assert result.is_filled is True

    def test_returns_none_on_timeout(self, tmp_path):
        result = poll_for_feedback(str(tmp_path), "nonexistent_id", timeout=0.3, interval=0.1)
        assert result is None

    def test_finds_file_appearing_mid_poll(self, tmp_path):
        action_id = "EURUSD_20260523_142303_c4f0d3e4"
        feedback_path = str(tmp_path / f"{action_id}_result.txt")

        def write_after_delay():
            time.sleep(0.3)
            write_feedback(feedback_path, status="FILLED", tickets="999", error_code="0")

        t = threading.Thread(target=write_after_delay, daemon=True)
        t.start()

        result = poll_for_feedback(str(tmp_path), action_id, timeout=5.0, interval=0.1)
        t.join()

        assert result is not None
        assert result.is_filled is True
        assert result.tickets == ["999"]

    def test_poll_respects_interval(self, tmp_path):
        """Timeout of 0.2s with 0.1s interval should make at most ~2 checks."""
        start = time.monotonic()
        poll_for_feedback(str(tmp_path), "no_file", timeout=0.2, interval=0.1)
        elapsed = time.monotonic() - start
        # Should take roughly 0.2s, not much more
        assert elapsed < 1.0

    def test_returns_parsed_record(self, tmp_path):
        action_id = "XAUUSD_20260523_150000_deadbeef"
        path = str(tmp_path / f"{action_id}_result.txt")
        write_feedback(
            path,
            id=action_id,
            status="FILLED",
            asset="XAUUSDm",
            action="OPEN",
            side="SELL",
            size="0.05",
            tickets="123456",
            avg_price="2345.67",
            message="",
            error_code="0",
        )
        r = poll_for_feedback(str(tmp_path), action_id, timeout=5.0)
        assert r is not None
        assert r.asset     == "XAUUSDm"
        assert r.side      == "SELL"
        assert r.size      == pytest.approx(0.05)
        assert r.avg_price == pytest.approx(2345.67)
        assert r.tickets   == ["123456"]
