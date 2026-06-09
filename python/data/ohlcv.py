"""
python/data/ohlcv.py

OHLCV data ingestion module — Phase 10.

Fetches candlestick data from Yahoo Finance (yfinance), normalises broker
symbols to yfinance tickers, and persists to CSV for offline use by the
strategy and regime-detection layers.

Supported timeframes (MT4 notation → yfinance interval):
  M1  → 1m   (max 7 days history from yfinance)
  M5  → 5m   (max 60 days)
  M15 → 15m  (max 60 days)
  M30 → 30m  (max 60 days)
  H1  → 1h   (max 730 days)
  D1  → 1d   (unlimited)
  W1  → 1wk  (unlimited)

Symbol normalisation strips broker suffixes (e.g. Exness 'm') then maps
to the correct yfinance ticker:
  EURUSDm / EURUSD → EURUSD=X
  XAUUSDm / XAUUSD → GC=F
  BTCUSDm / BTCUSD → BTC-USD

CSV storage layout (one file per symbol + timeframe):
  {data_dir}/{SYMBOL}_{TIMEFRAME}.csv
  e.g.  python/data/csv/EURUSD_H1.csv

DataFrame contract:
  - Index: 'datetime', tz-aware UTC (DatetimeTZDtype)
  - Columns: open, high, low, close, volume (float64)
  - Sorted ascending by datetime
  - No duplicate timestamps
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf


# ---------------------------------------------------------------------------
# Symbol and timeframe maps
# ---------------------------------------------------------------------------

# Maps normalised (no-suffix) broker symbol → yfinance ticker
_SYMBOL_MAP: dict[str, str] = {
    # Forex majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    # Forex crosses
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURCHF": "EURCHF=X",
    "AUDCAD": "AUDCAD=X",
    "AUDNZD": "AUDNZD=X",
    # Commodities
    "XAUUSD": "GC=F",    # Gold futures
    "XAGUSD": "SI=F",    # Silver futures
    "WTIUSD": "CL=F",    # WTI crude oil
    # Crypto
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "BNBUSD": "BNB-USD",
    # Indices (common CFD names)
    "US30":   "^DJI",
    "US500":  "^GSPC",
    "USTEC":  "^NDX",
    "UK100":  "^FTSE",
    "GER40":  "^GDAXI",
}

# Maps MT4 timeframe string → yfinance interval string
_TIMEFRAME_MAP: dict[str, str] = {
    "M1":  "1m",
    "M5":  "5m",
    "M15": "15m",
    "M30": "30m",
    "H1":  "1h",
    "H4":  "1h",   # fetched as 1h, resampled to 4h inside fetch_ohlcv
    "D1":  "1d",
    "W1":  "1wk",
}

# Expected DataFrame columns (lowercase, matches yfinance output after rename)
_COLUMNS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

def normalise_symbol(broker_symbol: str) -> str:
    """Strip broker suffix and return the yfinance ticker for *broker_symbol*.

    Strips a trailing lowercase 'm' (Exness convention) and any surrounding
    whitespace before looking up the ticker.

    Args:
        broker_symbol: Symbol as used in MT4, e.g. "EURUSDm", "EURUSD",
                       "XAUUSDm", "BTC-USD".

    Returns:
        yfinance ticker string, e.g. "EURUSD=X", "GC=F", "BTC-USD".

    Raises:
        ValueError: if the symbol is not in the known symbol map.
    """
    raw = broker_symbol.strip()

    # Strip trailing lowercase 'm' (Exness broker suffix)
    if raw.endswith("m") and len(raw) > 1:
        candidate = raw[:-1].upper()
    else:
        candidate = raw.upper()

    if candidate not in _SYMBOL_MAP:
        raise ValueError(
            f"normalise_symbol: unknown symbol '{broker_symbol}'. "
            f"Known symbols: {sorted(_SYMBOL_MAP)}"
        )

    return _SYMBOL_MAP[candidate]


def broker_to_storage_symbol(broker_symbol: str) -> str:
    """Return the clean uppercase symbol used for CSV file naming.

    Strips broker suffixes so 'EURUSDm' and 'EURUSD' both map to 'EURUSD'.

    Args:
        broker_symbol: e.g. "EURUSDm"

    Returns:
        Uppercase clean symbol, e.g. "EURUSD"
    """
    raw = broker_symbol.strip()
    if raw.endswith("m") and len(raw) > 1:
        return raw[:-1].upper()
    return raw.upper()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_ohlcv(
    broker_symbol: str,
    timeframe: str,
    start: str,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch OHLCV candles from Yahoo Finance.

    Args:
        broker_symbol: MT4 symbol, e.g. "EURUSDm" or "EURUSD".
        timeframe:     MT4 timeframe string: M1, M5, M15, M30, H1, H4, D1, W1.
        start:         Start date string, e.g. "2025-01-01".
        end:           End date string (exclusive), e.g. "2026-01-01".
                       Defaults to today (UTC).

    Returns:
        DataFrame with DatetimeTZDtype UTC index named 'datetime' and
        columns [open, high, low, close, volume] as float64.
        Rows are sorted ascending, no duplicate timestamps.

    Raises:
        ValueError: if timeframe or symbol is unknown, or if yfinance
                    returns an empty DataFrame (bad date range / no data).
        IOError:    if the yfinance download fails.
    """
    timeframe_upper = timeframe.upper()
    if timeframe_upper not in _TIMEFRAME_MAP:
        raise ValueError(
            f"fetch_ohlcv: unknown timeframe '{timeframe}'. "
            f"Supported: {sorted(_TIMEFRAME_MAP)}"
        )

    ticker   = normalise_symbol(broker_symbol)
    interval = _TIMEFRAME_MAP[timeframe_upper]
    end_str  = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        raw = yf.download(
            ticker,
            start=start,
            end=end_str,
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        raise IOError(
            f"fetch_ohlcv: yfinance download failed for '{ticker}': {exc}"
        ) from exc

    if raw.empty:
        raise ValueError(
            f"fetch_ohlcv: no data returned for '{broker_symbol}' "
            f"({ticker}) timeframe={timeframe} start={start} end={end_str}"
        )

    df = _normalise_dataframe(raw)

    # Resample 1h → 4h for H4 timeframe
    if timeframe_upper == "H4":
        df = _resample_to_4h(df)

    return df


def _normalise_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """Rename columns, set UTC index, sort, drop duplicates."""
    # yfinance column names vary by version — handle MultiIndex and flat
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.droplevel(level=1, axis=1)

    raw.columns = [c.lower() for c in raw.columns]

    # Keep only the columns we care about; add volume=0 if missing
    for col in _COLUMNS:
        if col not in raw.columns:
            raw[col] = 0.0

    df = raw[_COLUMNS].copy()
    df.index.name = "datetime"

    # Ensure UTC timezone
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.astype(float)

    return df


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-hour OHLCV data to 4-hour bars."""
    resampled = df.resample("4h").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return resampled


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _csv_path(data_dir: str, broker_symbol: str, timeframe: str) -> str:
    """Build the CSV file path for a given symbol and timeframe."""
    symbol = broker_to_storage_symbol(broker_symbol)
    return os.path.join(data_dir, f"{symbol}_{timeframe.upper()}.csv")


def save_ohlcv(
    df: pd.DataFrame,
    data_dir: str,
    broker_symbol: str,
    timeframe: str,
) -> str:
    """Persist an OHLCV DataFrame to CSV.

    Overwrites any existing file for this symbol+timeframe pair.
    Creates *data_dir* if it does not exist.

    Args:
        df:            DataFrame from fetch_ohlcv (UTC index, OHLCV columns).
        data_dir:      Directory to store CSV files, e.g. "python/data/csv".
        broker_symbol: e.g. "EURUSDm" (used to derive filename).
        timeframe:     e.g. "H1".

    Returns:
        Full path of the written CSV file.

    Raises:
        IOError: if the file cannot be written.
    """
    os.makedirs(data_dir, exist_ok=True)
    path = _csv_path(data_dir, broker_symbol, timeframe)

    try:
        df.to_csv(path, index=True)
    except OSError as exc:
        raise IOError(f"save_ohlcv: cannot write '{path}': {exc}") from exc

    return path


def load_ohlcv(
    data_dir: str,
    broker_symbol: str,
    timeframe: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Load a previously saved OHLCV CSV from *data_dir*.

    Args:
        data_dir:      Directory containing CSV files.
        broker_symbol: e.g. "EURUSDm".
        timeframe:     e.g. "H1".
        start:         Optional ISO date string to filter rows from.
        end:           Optional ISO date string to filter rows until (inclusive).

    Returns:
        DataFrame with UTC DatetimeTZDtype index and OHLCV float64 columns.
        Sorted ascending, no duplicate timestamps.

    Raises:
        IOError:   if the CSV file does not exist.
        ValueError: if the CSV is empty or malformed.
    """
    path = _csv_path(data_dir, broker_symbol, timeframe)

    if not os.path.isfile(path):
        raise IOError(
            f"load_ohlcv: no CSV found at '{path}'. "
            f"Run fetch_ohlcv + save_ohlcv first."
        )

    try:
        df = pd.read_csv(path, index_col="datetime", parse_dates=True)
    except Exception as exc:
        raise ValueError(f"load_ohlcv: failed to parse '{path}': {exc}") from exc

    if df.empty:
        raise ValueError(f"load_ohlcv: CSV is empty: '{path}'")

    # Re-apply UTC timezone if stripped by CSV round-trip
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df.astype(float)

    # Apply date filters
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]

    return df


# ---------------------------------------------------------------------------
# Update (fetch latest and merge)
# ---------------------------------------------------------------------------

def update_ohlcv(
    data_dir: str,
    broker_symbol: str,
    timeframe: str,
    lookback_days: int = 30,
) -> pd.DataFrame:
    """Fetch the latest candles and merge with any existing CSV.

    Fetches the most recent *lookback_days* of data, merges with the existing
    CSV (if any), deduplicates, and saves back. Safe to call repeatedly.

    Args:
        data_dir:      Directory containing CSV files.
        broker_symbol: e.g. "EURUSDm".
        timeframe:     e.g. "H1".
        lookback_days: Number of calendar days to fetch. Default 30.

    Returns:
        The full merged DataFrame after update.

    Raises:
        ValueError: if the symbol or timeframe is unknown.
        IOError:    if yfinance download or file write fails.
    """
    start = (
        pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")

    new_df = fetch_ohlcv(broker_symbol, timeframe, start=start)

    # Load existing data if present, otherwise start fresh
    path = _csv_path(data_dir, broker_symbol, timeframe)
    if os.path.isfile(path):
        try:
            existing = load_ohlcv(data_dir, broker_symbol, timeframe)
            merged = pd.concat([existing, new_df])
            merged = merged[~merged.index.duplicated(keep="last")]
            merged = merged.sort_index()
        except (IOError, ValueError):
            merged = new_df
    else:
        merged = new_df

    save_ohlcv(merged, data_dir, broker_symbol, timeframe)
    return merged
