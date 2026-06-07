"""
python/tests/test_action_writer.py

Unit tests for python/bridge/action_writer.py.

Tests cover:
  - write_action_file: file contents, atomic write (.tmp removed after rename),
    IOError on bad folder
  - generate_action_id: format, uniqueness, uppercase normalisation, ValueError
    on empty asset
  - write_open_action: correct fields written, ValueError on bad side/size/asset,
    returns valid path
  - write_close_all_action: correct fields written, ValueError on empty asset,
    returns valid path

Run with: python -m pytest python/tests/test_action_writer.py -v
"""

import os
import re
import tempfile
import pytest

from bridge.action_writer import (
    write_action_file,
    generate_action_id,
    write_open_action,
    write_close_all_action,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_fields(path: str) -> dict:
    """Parse a key=value file into a dict. Used to verify written files."""
    fields = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value
    return fields


# ---------------------------------------------------------------------------
# write_action_file
# ---------------------------------------------------------------------------

class TestWriteActionFile:

    def test_writes_correct_key_value_pairs(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, id="abc123", action="OPEN", asset="EURUSD")
        fields = read_fields(path)
        assert fields["id"]     == "abc123"
        assert fields["action"] == "OPEN"
        assert fields["asset"]  == "EURUSD"

    def test_none_value_written_as_empty_string(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, sl=None, tp=None)
        fields = read_fields(path)
        assert fields["sl"] == ""
        assert fields["tp"] == ""

    def test_field_order_preserved(self, tmp_path):
        """Keys must appear in insertion order (Python 3.7+ dict guarantee)."""
        path = str(tmp_path / "test.txt")
        write_action_file(path, id="x", asset="Y", action="Z")
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if "=" in l]
        assert lines[0].startswith("id=")
        assert lines[1].startswith("asset=")
        assert lines[2].startswith("action=")

    def test_tmp_file_removed_after_write(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, id="abc")
        assert not os.path.exists(path + ".tmp")

    def test_final_file_exists_after_write(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, id="abc")
        assert os.path.exists(path)

    def test_ioerror_on_missing_folder(self):
        bad_path = "/nonexistent_folder_xyz/test.txt"
        with pytest.raises(IOError):
            write_action_file(bad_path, id="abc")

    def test_utf8_encoding(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, comment="euro€sign")
        with open(path, "rb") as f:
            raw = f.read()
        assert "euro€sign".encode("utf-8") in raw

    def test_unix_line_endings(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_action_file(path, id="abc", action="OPEN")
        with open(path, "rb") as f:
            raw = f.read()
        assert b"\r\n" not in raw   # no Windows line endings
        assert b"\n"   in raw       # Unix endings present


# ---------------------------------------------------------------------------
# generate_action_id
# ---------------------------------------------------------------------------

class TestGenerateActionId:

    def test_format_matches_spec(self):
        """ID must match ASSET_YYYYMMDD_HHMMSS_xxxxxxxx"""
        action_id = generate_action_id("eurusd")
        pattern = r"^[A-Z]+_\d{8}_\d{6}_[0-9a-f]{8}$"
        assert re.match(pattern, action_id), f"ID '{action_id}' does not match pattern"

    def test_asset_normalised_to_uppercase(self):
        action_id = generate_action_id("eurusd")
        assert action_id.startswith("EURUSD_")

    def test_two_ids_are_unique(self):
        id1 = generate_action_id("EURUSD")
        id2 = generate_action_id("EURUSD")
        assert id1 != id2

    def test_valueerror_on_empty_asset(self):
        with pytest.raises(ValueError):
            generate_action_id("")

    def test_valueerror_on_whitespace_asset(self):
        with pytest.raises(ValueError):
            generate_action_id("   ")


# ---------------------------------------------------------------------------
# write_open_action
# ---------------------------------------------------------------------------

class TestWriteOpenAction:

    def test_writes_all_required_fields(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01)
        fields = read_fields(path)
        assert fields["asset"]      == "EURUSD"
        assert fields["action"]     == "OPEN"
        assert fields["side"]       == "BUY"
        assert fields["size"]       == "0.01"
        assert fields["order_type"] == "MARKET"

    def test_sl_tp_zero_written_as_blank(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01, sl=0.0, tp=0.0)
        fields = read_fields(path)
        assert fields["sl"] == ""
        assert fields["tp"] == ""

    def test_sl_tp_nonzero_written_correctly(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01,
                                  sl=1.0800, tp=1.0900)
        fields = read_fields(path)
        assert fields["sl"] == "1.08"
        assert fields["tp"] == "1.09"

    def test_magic_zero_written_as_blank(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01, magic=0)
        fields = read_fields(path)
        assert fields["magic_number"] == ""

    def test_magic_nonzero_written_correctly(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01, magic=999)
        fields = read_fields(path)
        assert fields["magic_number"] == "999"

    def test_side_normalised_to_uppercase(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "sell", 0.01)
        fields = read_fields(path)
        assert fields["side"] == "SELL"

    def test_asset_preserves_broker_casing(self, tmp_path):
        """asset field must preserve original casing — brokers like Exness use 'EURUSDm'."""
        path = write_open_action(str(tmp_path), "EURUSDm", "BUY", 0.01)
        fields = read_fields(path)
        assert fields["asset"] == "EURUSDm"

    def test_asset_strips_whitespace(self, tmp_path):
        path = write_open_action(str(tmp_path), "  EURUSD  ", "BUY", 0.01)
        fields = read_fields(path)
        assert fields["asset"] == "EURUSD"

    def test_returns_path_that_exists(self, tmp_path):
        path = write_open_action(str(tmp_path), "EURUSD", "BUY", 0.01)
        assert os.path.exists(path)

    def test_valueerror_on_invalid_side(self, tmp_path):
        with pytest.raises(ValueError, match="side"):
            write_open_action(str(tmp_path), "EURUSD", "LONG", 0.01)

    def test_valueerror_on_zero_size(self, tmp_path):
        with pytest.raises(ValueError, match="size"):
            write_open_action(str(tmp_path), "EURUSD", "BUY", 0.0)

    def test_valueerror_on_negative_size(self, tmp_path):
        with pytest.raises(ValueError, match="size"):
            write_open_action(str(tmp_path), "EURUSD", "BUY", -0.01)

    def test_valueerror_on_empty_asset(self, tmp_path):
        with pytest.raises(ValueError, match="asset"):
            write_open_action(str(tmp_path), "", "BUY", 0.01)

    def test_no_file_written_on_validation_failure(self, tmp_path):
        """Fail fast — no .txt or .tmp left behind on ValueError."""
        with pytest.raises(ValueError):
            write_open_action(str(tmp_path), "EURUSD", "LONG", 0.01)
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# write_close_all_action
# ---------------------------------------------------------------------------

class TestWriteCloseAllAction:

    def test_writes_correct_action_field(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "EURUSD")
        fields = read_fields(path)
        assert fields["action"] == "CLOSE_ALL"
        assert fields["asset"]  == "EURUSD"

    def test_side_size_order_type_blank(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "EURUSD")
        fields = read_fields(path)
        assert fields["side"]       == ""
        assert fields["size"]       == ""
        assert fields["order_type"] == ""

    def test_magic_zero_written_as_blank(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "EURUSD", magic=0)
        fields = read_fields(path)
        assert fields["magic_number"] == ""

    def test_magic_nonzero_written_correctly(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "EURUSD", magic=12345)
        fields = read_fields(path)
        assert fields["magic_number"] == "12345"

    def test_returns_path_that_exists(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "EURUSD")
        assert os.path.exists(path)

    def test_valueerror_on_empty_asset(self, tmp_path):
        with pytest.raises(ValueError, match="asset"):
            write_close_all_action(str(tmp_path), "")

    def test_asset_preserves_broker_casing(self, tmp_path):
        """asset field must preserve original casing — brokers like Exness use 'EURUSDm'."""
        path = write_close_all_action(str(tmp_path), "EURUSDm")
        fields = read_fields(path)
        assert fields["asset"] == "EURUSDm"

    def test_asset_strips_whitespace(self, tmp_path):
        path = write_close_all_action(str(tmp_path), "  xauusd  ")
        fields = read_fields(path)
        assert fields["asset"] == "xauusd"
