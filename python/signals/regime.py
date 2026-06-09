"""
python/signals/regime.py

Market regime detection — Phase 11.

Classifies each bar of an OHLCV DataFrame into one of four regimes:

  TRENDING_UP   — ADX above threshold, price moving higher
  TRENDING_DOWN — ADX above threshold, price moving lower
  RANGING       — ADX below threshold, volatility normal
  VOLATILE      — ADX below threshold, volatility elevated
  UNKNOWN       — insufficient data to classify (warm-up bars)

Algorithm:
  1. ATR (Wilder, period=14) — measures true volatility per bar
  2. ADX (Wilder, period=14) — measures trend strength (direction-agnostic)
  3. MA slope (SMA period=20, compared to N/2 bars ago) — trend direction
  4. ATR ratio = ATR / close  — normalised volatility
  5. Vol threshold = rolling 75th percentile of ATR ratio (200-bar window)

  Priority:
    ADX > adx_threshold  →  TRENDING_UP or TRENDING_DOWN (via MA slope)
    ATR ratio > vol_threshold  →  VOLATILE
    otherwise  →  RANGING
    too few bars  →  UNKNOWN

Pure pandas/numpy — no TA-Lib or external indicator library required.
Designed to consume DataFrames produced by data/ohlcv.py.

Minimum bars needed for meaningful output: ~60
  (14 ATR warm-up + 14 ADX warm-up + 20 MA + 50 vol percentile window)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Regime label
# ---------------------------------------------------------------------------

class RegimeLabel(str, Enum):
    """Market regime classification label.

    Inherits from str so labels compare equal to strings and serialise
    naturally to CSV/JSON without extra conversion.
    """
    TRENDING_UP   = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING       = "RANGING"
    VOLATILE      = "VOLATILE"
    UNKNOWN       = "UNKNOWN"   # warm-up or insufficient data

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (exponential with alpha=1/period, no bias correction)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Indicator functions (public — usable independently by strategy modules)
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder smoothing.

    Args:
        df:     OHLCV DataFrame with columns [open, high, low, close, volume].
        period: Smoothing period. Default 14.

    Returns:
        pd.Series aligned to df.index. First row is NaN.
    """
    high       = df["high"]
    low        = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    return _wilder_smooth(tr, period)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index using Wilder smoothing.

    Measures trend *strength* — not direction. Values above 25 indicate
    a trending market; below 20 indicates a non-trending (ranging) market.

    Args:
        df:     OHLCV DataFrame.
        period: Smoothing period. Default 14.

    Returns:
        pd.Series of ADX values aligned to df.index. Early rows are NaN.
    """
    high = df["high"]
    low  = df["low"]

    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = up_move.where((up_move > down_move)   & (up_move > 0),   0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = compute_atr(df, period)

    # Protect against zero ATR (flat/illiquid markets)
    atr_safe = atr.replace(0, np.nan)

    plus_di  = 100.0 * _wilder_smooth(plus_dm,  period) / atr_safe
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / atr_safe

    di_sum  = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()

    # Protect against zero sum
    dx = (100.0 * di_diff / di_sum.replace(0, np.nan))

    return _wilder_smooth(dx, period)


def compute_ma_slope(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Simple Moving Average slope direction.

    Compares the current SMA to its value period//2 bars ago.

    Returns:
        pd.Series of float:
          +1.0 = MA rising   (bullish)
           0.0 = MA flat
          -1.0 = MA falling  (bearish)
    """
    ma    = df["close"].rolling(period).mean()
    delta = ma.diff(max(1, period // 2))
    return np.sign(delta)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_regime(
    df: pd.DataFrame,
    adx_period: int         = 14,
    atr_period: int         = 14,
    ma_period: int          = 20,
    adx_trend_threshold: float = 25.0,
    vol_percentile: float   = 75.0,
    vol_window: int         = 200,
) -> pd.Series:
    """Classify each bar of *df* into a RegimeLabel.

    Args:
        df:                   OHLCV DataFrame from data/ohlcv.py.
        adx_period:           Period for ADX computation. Default 14.
        atr_period:           Period for ATR computation. Default 14.
        ma_period:            Period for SMA slope computation. Default 20.
        adx_trend_threshold:  ADX level above which market is considered
                              trending. Default 25.0.
        vol_percentile:       Percentile of the rolling ATR-ratio used as
                              the volatility threshold. Default 75.0.
        vol_window:           Look-back window for the vol percentile
                              calculation. Default 200, min_periods=50.

    Returns:
        pd.Series of RegimeLabel values with the same index as *df*.
        Bars with insufficient history are labelled UNKNOWN.
    """
    adx       = compute_adx(df, adx_period)
    atr       = compute_atr(df, atr_period)
    ma_slope  = compute_ma_slope(df, ma_period)

    # Normalised volatility: ATR relative to price level
    atr_ratio = atr / df["close"].replace(0, np.nan)

    # Rolling high-volatility threshold
    vol_threshold = atr_ratio.rolling(
        vol_window, min_periods=50
    ).quantile(vol_percentile / 100.0)

    # Start everyone as UNKNOWN
    regimes = pd.Series(RegimeLabel.UNKNOWN, index=df.index, dtype=object)

    # Classify in priority order (later assignments overwrite earlier ones)
    has_data   = adx.notna() & atr_ratio.notna() & ma_slope.notna()
    is_trend   = has_data & (adx > adx_trend_threshold)
    no_trend   = has_data & (adx <= adx_trend_threshold)
    has_vol_th = vol_threshold.notna()
    is_high_vol = no_trend & has_vol_th & (atr_ratio > vol_threshold)

    regimes[no_trend & (~is_high_vol | ~has_vol_th)] = RegimeLabel.RANGING
    regimes[is_high_vol]                              = RegimeLabel.VOLATILE
    regimes[is_trend & (ma_slope >= 0)]               = RegimeLabel.TRENDING_UP
    regimes[is_trend & (ma_slope <  0)]               = RegimeLabel.TRENDING_DOWN

    return regimes


def current_regime(df: pd.DataFrame, **kwargs) -> RegimeLabel:
    """Return the regime of the most recent fully-classified bar.

    Convenience wrapper around classify_regime. Skips UNKNOWN bars when
    looking for the latest label (warm-up NaN rows don't pollute the result).

    Args:
        df:      OHLCV DataFrame (minimum ~60 bars recommended).
        **kwargs: Forwarded to classify_regime.

    Returns:
        RegimeLabel for the latest classified bar, or RegimeLabel.UNKNOWN
        if the DataFrame is too short for any classification.
    """
    regimes = classify_regime(df, **kwargs)
    known   = regimes[regimes != RegimeLabel.UNKNOWN]

    if known.empty:
        return RegimeLabel.UNKNOWN

    return known.iloc[-1]
