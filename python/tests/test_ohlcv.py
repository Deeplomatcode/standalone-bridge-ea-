"""
python/tests/test_ohlcv.py

Unit tests for data/ohlcv.py.

yfinance.download is mocked throughout — no network calls made.

Tests cover:
  - normalise_symbol: known pairs, broker suffix stripping, unknown raises ValueError
  - broker_to_storage_symbol: suffix stripping, uppercasing
  - fetch_ohlcv: correct ticker/interval passed, DataFrame shape, H4 resampling,
    unknown timeframe/symbol raises ValueError, empty return raises ValueError
  - save_ohlcv + load_ohlcv: round-trip preserves data, creates dir,
    missing file raises IOError, date filters work
  - update_ohlcv: merges with existing, deduplicates, creates fresh if no existing
"""

import os
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from data.ohlcv import (
    normalise_symbol,
    broker_to_storage_symbol,
    fetch_ohlcv,
    save_ohlcv,
    load_ohlcv,
    update_ohlcv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ohlcv_df(n: int = 5, freq: str = "1h", start: str = "2026-01-01") -> pd.DataFrame:
    """Create a minimal OHLCV DataFrame with UTC DatetimeTZDtype index."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="datetime")
    return pd.DataFrame({
        "open":   [1.10 + i * 0.001 for i in range(n)],
        "high":   [1.11 + i * 0.001 for i in range(n)],
        "low":    [1.09 + i * 0.001 for i in range(n)],
        "close":  [1.105 + i * 0.001 for i in range(n)],
        "volume": [1000.0 + i * 10 for i in range(n)],
    }, index=idx)


def make_yf_raw(n: int = 5, freq: str = "1h", start: str = "2026-01-01") -> pd.DataFrame:
    """Simulate a yfinance.download() return value (Title-cased columns)."""
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC", name="Datetime")
    return pd.DataFrame({
        "Open":   [1.10 + i * 0.001 for i in range(n)],
        "High":   [1.11 + i * 0.001 for i in range(n)],
        "Low":    [1.09 + i * 0.001 for i in range(n)],
        "Close":  [1.105 + i * 0.001 for i in range(n)],
        "Volume": [1000.0 + i * 10 for i in range(n)],
    }, index=idx)


# ---------------------------------------------------------------------------
# normalise_symbol
# ---------------------------------------------------------------------------

class TestNormaliseSymbol:

    def test_eurusd_plain(self):
        assert normalise_symbol("EURUSD") == "EURUSD=X"

    def test_eurusd_lowercase_m_suffix(self):
        assert normalise_symbol("EURUSDm") == "EURUSD=X"

    def test_xauusd_maps_to_gold_futures(self):
        assert normalise_symbol("XAUUSD") == "GC=F"

    def test_xauusdm_strips_suffix(self):
        assert normalise_symbol("XAUUSDm") == "GC=F"

    def test_btcusd_maps_correctly(self):
        assert normalise_symbol("BTCUSDm") == "BTC-USD"

    def test_strips_whitespace(self):
        assert normalise_symbol("  EURUSDm  ") == "EURUSD=X"

    def test_unknown_symbol_raises_valueerror(self):
        with pytest.raises(ValueError, match="unknown symbol"):
            normalise_symbol("FAKEUSD")

    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError):
            normalise_symbol("")

    def test_gbpusd(self):
        assert normalise_symbol("GBPUSDm") == "GBPUSD=X"


# ---------------------------------------------------------------------------
# broker_to_storage_symbol
# ---------------------------------------------------------------------------

class TestBrokerToStorageSymbol:

    def test_strips_m_suffix(self):
        assert broker_to_storage_symbol("EURUSDm") == "EURUSD"

    def test_uppercase(self):
        assert broker_to_storage_symbol("eurusd") == "EURUSD"

    def test_no_suffix_unchanged(self):
        assert broker_to_storage_symbol("EURUSD") == "EURUSD"

    def test_strips_whitespace(self):
        assert broker_to_storage_symbol("  EURUSDm  ") == "EURUSD"


# ---------------------------------------------------------------------------
# fetch_ohlcv
# ---------------------------------------------------------------------------

class TestFetchOHLCV:

    @patch("data.ohlcv.yf.download")
    def test_correct_ticker_passed(self, mock_dl):
        mock_dl.return_value = make_yf_raw()
        fetch_ohlcv("EURUSDm", "H1", "2026-01-01", "2026-01-06")
        call_args = mock_dl.call_args
        assert call_args[0][0] == "EURUSD=X"

    @patch("data.ohlcv.yf.download")
    def test_correct_interval_passed_h1(self, mock_dl):
        mock_dl.return_value = make_yf_raw()
        fetch_ohlcv("EURUSD", "H1", "2026-01-01", "2026-01-06")
        assert mock_dl.call_args[1]["interval"] == "1h"

    @patch("data.ohlcv.yf.download")
    def test_correct_interval_passed_d1(self, mock_dl):
        mock_dl.return_value = make_yf_raw(freq="1D")
        fetch_ohlcv("EURUSD", "D1", "2026-01-01", "2026-01-06")
        assert mock_dl.call_args[1]["interval"] == "1d"

    @patch("data.ohlcv.yf.download")
    def test_returns_dataframe_with_correct_columns(self, mock_dl):
        mock_dl.return_value = make_yf_raw()
        df = fetch_ohlcv("EURUSDm", "H1", "2026-01-01", "2026-01-06")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

    @patch("data.ohlcv.yf.download")
    def test_index_is_utc(self, mock_dl):
        mock_dl.return_value = make_yf_raw()
        df = fetch_ohlcv("EURUSDm", "H1", "2026-01-01", "2026-01-06")
        assert str(df.index.tzinfo) == "UTC"

    @patch("data.ohlcv.yf.download")
    def test_h4_resampled_to_4h_bars(self, mock_dl):
        # 8 x 1h bars → 2 x 4h bars
        mock_dl.return_value = make_yf_raw(n=8, freq="1h")
        df = fetch_ohlcv("EURUSD", "H4", "2026-01-01", "2026-01-06")
        assert len(df) == 2

    @patch("data.ohlcv.yf.download")
    def test_h4_open_is_first_bar_of_block(self, mock_dl):
        mock_dl.return_value = make_yf_raw(n=4, freq="1h")
        df = fetch_ohlcv("EURUSD", "H4", "2026-01-01", "2026-01-06")
        assert df.iloc[0]["open"] == pytest.approx(1.100)

    @patch("data.ohlcv.yf.download")
    def test_empty_return_raises_valueerror(self, mock_dl):
        mock_dl.return_value = pd.DataFrame()
        with pytest.raises(ValueError, match="no data returned"):
            fetch_ohlcv("EURUSD", "H1", "2026-01-01", "2026-01-06")

    def test_unknown_timeframe_raises_valueerror(self):
        with pytest.raises(ValueError, match="unknown timeframe"):
            fetch_ohlcv("EURUSD", "H8", "2026-01-01")

    def test_unknown_symbol_raises_valueerror(self):
        with pytest.raises(ValueError, match="unknown symbol"):
            fetch_ohlcv("FAKEUSD", "H1", "2026-01-01")

    @patch("data.ohlcv.yf.download")
    def test_yfinance_exception_raises_ioerror(self, mock_dl):
        mock_dl.side_effect = RuntimeError("network error")
        with pytest.raises(IOError, match="yfinance download failed"):
            fetch_ohlcv("EURUSD", "H1", "2026-01-01")

    @patch("data.ohlcv.yf.download")
    def test_no_duplicate_timestamps(self, mock_dl):
        raw = make_yf_raw(n=3)
        # Duplicate the first row
        raw = pd.concat([raw, raw.iloc[:1]])
        mock_dl.return_value = raw
        df = fetch_ohlcv("EURUSD", "H1", "2026-01-01")
        assert df.index.duplicated().sum() == 0


# ---------------------------------------------------------------------------
# save_ohlcv + load_ohlcv
# ---------------------------------------------------------------------------

class TestSaveLoadOHLCV:

    def test_round_trip_preserves_values(self, tmp_path):
        df = make_ohlcv_df()
        save_ohlcv(df, str(tmp_path), "EURUSDm", "H1")
        loaded = load_ohlcv(str(tmp_path), "EURUSDm", "H1")
        pd.testing.assert_frame_equal(df, loaded, check_freq=False)

    def test_creates_data_dir_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "subdir" / "data")
        df = make_ohlcv_df()
        save_ohlcv(df, new_dir, "EURUSD", "D1")
        assert os.path.isdir(new_dir)

    def test_csv_filename_uses_clean_symbol(self, tmp_path):
        df = make_ohlcv_df()
        path = save_ohlcv(df, str(tmp_path), "EURUSDm", "H1")
        assert os.path.basename(path) == "EURUSD_H1.csv"

    def test_load_missing_file_raises_ioerror(self, tmp_path):
        with pytest.raises(IOError, match="no CSV found"):
            load_ohlcv(str(tmp_path), "EURUSD", "H1")

    def test_loaded_index_is_utc(self, tmp_path):
        df = make_ohlcv_df()
        save_ohlcv(df, str(tmp_path), "EURUSD", "H1")
        loaded = load_ohlcv(str(tmp_path), "EURUSD", "H1")
        assert str(loaded.index.tzinfo) == "UTC"

    def test_load_start_filter(self, tmp_path):
        df = make_ohlcv_df(n=10)
        save_ohlcv(df, str(tmp_path), "EURUSD", "H1")
        loaded = load_ohlcv(str(tmp_path), "EURUSD", "H1", start="2026-01-01 05:00")
        assert loaded.index.min() >= pd.Timestamp("2026-01-01 05:00", tz="UTC")

    def test_load_end_filter(self, tmp_path):
        df = make_ohlcv_df(n=10)
        save_ohlcv(df, str(tmp_path), "EURUSD", "H1")
        loaded = load_ohlcv(str(tmp_path), "EURUSD", "H1", end="2026-01-01 03:00")
        assert loaded.index.max() <= pd.Timestamp("2026-01-01 03:00", tz="UTC")

    def test_broker_symbol_with_suffix_same_file_as_without(self, tmp_path):
        df = make_ohlcv_df()
        save_ohlcv(df, str(tmp_path), "EURUSDm", "H1")
        # Loading without 'm' suffix should find the same file
        loaded = load_ohlcv(str(tmp_path), "EURUSD", "H1")
        assert len(loaded) == len(df)


# ---------------------------------------------------------------------------
# update_ohlcv
# ---------------------------------------------------------------------------

class TestUpdateOHLCV:

    @patch("data.ohlcv.fetch_ohlcv")
    def test_creates_new_csv_if_none_exists(self, mock_fetch, tmp_path):
        new_data = make_ohlcv_df(n=5)
        mock_fetch.return_value = new_data
        result = update_ohlcv(str(tmp_path), "EURUSD", "H1")
        assert len(result) == 5

    @patch("data.ohlcv.fetch_ohlcv")
    def test_merges_with_existing_data(self, mock_fetch, tmp_path):
        existing = make_ohlcv_df(n=5, start="2026-01-01")
        save_ohlcv(existing, str(tmp_path), "EURUSD", "H1")

        # New data overlaps by 2 rows and adds 3 new rows
        new_data = make_ohlcv_df(n=5, start="2026-01-01 03:00")
        mock_fetch.return_value = new_data

        result = update_ohlcv(str(tmp_path), "EURUSD", "H1")
        # 5 existing + 3 genuinely new = 8 unique timestamps
        assert len(result) == 8

    @patch("data.ohlcv.fetch_ohlcv")
    def test_no_duplicate_timestamps_after_update(self, mock_fetch, tmp_path):
        existing = make_ohlcv_df(n=5)
        save_ohlcv(existing, str(tmp_path), "EURUSD", "H1")
        mock_fetch.return_value = existing  # exact same data
        result = update_ohlcv(str(tmp_path), "EURUSD", "H1")
        assert result.index.duplicated().sum() == 0

    @patch("data.ohlcv.fetch_ohlcv")
    def test_result_sorted_ascending(self, mock_fetch, tmp_path):
        mock_fetch.return_value = make_ohlcv_df(n=5)
        result = update_ohlcv(str(tmp_path), "EURUSD", "H1")
        assert result.index.is_monotonic_increasing

    @patch("data.ohlcv.fetch_ohlcv")
    def test_csv_written_after_update(self, mock_fetch, tmp_path):
        mock_fetch.return_value = make_ohlcv_df(n=3)
        update_ohlcv(str(tmp_path), "EURUSD", "H1")
        path = os.path.join(str(tmp_path), "EURUSD_H1.csv")
        assert os.path.isfile(path)
