"""
python/signals/order_blocks.py

Order Block detection — Phase 12a.

An Order Block (OB) is the last opposing candle before a significant
impulse move. It marks a price zone where institutional orders were
placed, and price tends to return to these zones before continuing.

  Bullish OB: last BEARISH candle before an upward impulse.
              Entry on retest from above. SL below OB low.
  Bearish OB: last BULLISH candle before a downward impulse.
              Entry on retest from below. SL above OB high.

Algorithm (per bar, looking back `lookback` bars):
  1. Measure the impulse: bullish = (current_high - lowest_low) / lowest_low
                          bearish = (highest_high - current_low) / highest_high
  2. If impulse > min_impulse_pct, search the lookback window for the
     last candle that is opposing to the impulse direction.
  3. That candle's high/low range is the OB zone.
  4. Each OB is emitted once (the first time the impulse is detected).

Detection is vectorised over the whole DataFrame and returns a list of
OrderBlock objects sorted by timestamp ascending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class OrderBlock:
    """A single detected order block zone.

    Attributes:
        timestamp:   Bar timestamp of the OB candle (the opposing candle).
        side:        "BULLISH" or "BEARISH".
        ob_high:     Upper edge of the OB zone (high of the OB candle).
        ob_low:      Lower edge of the OB zone (low of the OB candle).
        impulse_pct: Size of the triggering impulse move as a fraction of price.
        active:      True while the OB has not been fully consumed (price
                     has not closed through the opposite side of the zone).
    """
    timestamp:   pd.Timestamp
    side:        str        # "BULLISH" or "BEARISH"
    ob_high:     float
    ob_low:      float
    impulse_pct: float
    active:      bool = True

    @property
    def mid(self) -> float:
        """Midpoint of the OB zone."""
        return (self.ob_high + self.ob_low) / 2.0

    @property
    def height(self) -> float:
        """Height of the OB zone (ob_high - ob_low)."""
        return self.ob_high - self.ob_low


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _last_bearish_index(df: pd.DataFrame, start: int, end: int) -> Optional[int]:
    """Return iloc of the last bearish candle in df[start:end], or None."""
    for i in range(end - 1, start - 1, -1):
        if df["close"].iloc[i] < df["open"].iloc[i]:
            return i
    return None


def _last_bullish_index(df: pd.DataFrame, start: int, end: int) -> Optional[int]:
    """Return iloc of the last bullish candle in df[start:end], or None."""
    for i in range(end - 1, start - 1, -1):
        if df["close"].iloc[i] > df["open"].iloc[i]:
            return i
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_bullish_obs(
    df: pd.DataFrame,
    lookback: int = 10,
    min_impulse_pct: float = 0.003,
) -> List[OrderBlock]:
    """Detect bullish order blocks in *df*.

    A bullish OB is the last bearish candle before an upward impulse of
    at least *min_impulse_pct* relative to the swing low.

    Args:
        df:              OHLCV DataFrame from data/ohlcv.py.
        lookback:        Number of bars to look back when measuring the impulse
                         and searching for the opposing candle. Default 10.
        min_impulse_pct: Minimum impulse size as a fraction of price.
                         0.003 = 30 pips on EURUSD at 1.10. Default 0.003.

    Returns:
        List of OrderBlock (side="BULLISH"), sorted by timestamp ascending.
        Each OB is emitted once — the first bar where the impulse is detected.
    """
    obs: List[OrderBlock] = []
    seen_timestamps: set  = set()
    n = len(df)

    for i in range(lookback, n):
        window_start = i - lookback
        window_end   = i  # exclusive of current bar in window search

        lowest_low = df["low"].iloc[window_start:window_end].min()
        current_high = df["high"].iloc[i]

        if lowest_low <= 0:
            continue

        impulse = (current_high - lowest_low) / lowest_low

        if impulse < min_impulse_pct:
            continue

        ob_idx = _last_bearish_index(df, window_start, window_end)
        if ob_idx is None:
            continue

        ts = df.index[ob_idx]
        if ts in seen_timestamps:
            continue

        seen_timestamps.add(ts)
        obs.append(OrderBlock(
            timestamp   = ts,
            side        = "BULLISH",
            ob_high     = float(df["high"].iloc[ob_idx]),
            ob_low      = float(df["low"].iloc[ob_idx]),
            impulse_pct = float(impulse),
        ))

    return sorted(obs, key=lambda ob: ob.timestamp)


def find_bearish_obs(
    df: pd.DataFrame,
    lookback: int = 10,
    min_impulse_pct: float = 0.003,
) -> List[OrderBlock]:
    """Detect bearish order blocks in *df*.

    A bearish OB is the last bullish candle before a downward impulse of
    at least *min_impulse_pct* relative to the swing high.

    Args:
        df:              OHLCV DataFrame from data/ohlcv.py.
        lookback:        Number of bars to look back. Default 10.
        min_impulse_pct: Minimum impulse size as fraction of price. Default 0.003.

    Returns:
        List of OrderBlock (side="BEARISH"), sorted by timestamp ascending.
    """
    obs: List[OrderBlock] = []
    seen_timestamps: set  = set()
    n = len(df)

    for i in range(lookback, n):
        window_start  = i - lookback
        window_end    = i

        highest_high  = df["high"].iloc[window_start:window_end].max()
        current_low   = df["low"].iloc[i]

        if highest_high <= 0:
            continue

        impulse = (highest_high - current_low) / highest_high

        if impulse < min_impulse_pct:
            continue

        ob_idx = _last_bullish_index(df, window_start, window_end)
        if ob_idx is None:
            continue

        ts = df.index[ob_idx]
        if ts in seen_timestamps:
            continue

        seen_timestamps.add(ts)
        obs.append(OrderBlock(
            timestamp   = ts,
            side        = "BEARISH",
            ob_high     = float(df["high"].iloc[ob_idx]),
            ob_low      = float(df["low"].iloc[ob_idx]),
            impulse_pct = float(impulse),
        ))

    return sorted(obs, key=lambda ob: ob.timestamp)


def detect_order_blocks(
    df: pd.DataFrame,
    lookback: int = 10,
    min_impulse_pct: float = 0.003,
) -> List[OrderBlock]:
    """Detect all bullish and bearish order blocks in *df*.

    Combines find_bullish_obs and find_bearish_obs, returns all OBs
    sorted by timestamp ascending.

    Args:
        df:              OHLCV DataFrame.
        lookback:        Look-back window. Default 10.
        min_impulse_pct: Minimum impulse size. Default 0.003.

    Returns:
        Combined sorted list of all OrderBlock objects.
    """
    bullish = find_bullish_obs(df, lookback, min_impulse_pct)
    bearish = find_bearish_obs(df, lookback, min_impulse_pct)
    return sorted(bullish + bearish, key=lambda ob: ob.timestamp)


def price_in_zone(
    price: float,
    ob: OrderBlock,
    tolerance: float = 0.0,
) -> bool:
    """Return True if *price* is within (or near) the OB zone.

    Args:
        price:     Current bid/ask or mid price.
        ob:        The OrderBlock to check against.
        tolerance: Extend the zone by this amount on each side.
                   Useful for slightly imprecise retest entries. Default 0.0.

    Returns:
        True if ob_low - tolerance <= price <= ob_high + tolerance.
    """
    return (ob.ob_low - tolerance) <= price <= (ob.ob_high + tolerance)


def mark_mitigated(obs: List[OrderBlock], df: pd.DataFrame) -> List[OrderBlock]:
    """Mark OBs as inactive (active=False) when price has closed through them.

    A bullish OB is mitigated when price closes below its ob_low.
    A bearish OB is mitigated when price closes above its ob_high.

    Modifies OBs in-place and returns the same list.

    Args:
        obs: List of OrderBlock objects to evaluate.
        df:  OHLCV DataFrame with bars after each OB's timestamp.

    Returns:
        The same list with active flags updated.
    """
    for ob in obs:
        # Only look at bars after the OB formed
        future = df[df.index > ob.timestamp]
        if future.empty:
            continue

        if ob.side == "BULLISH":
            # Mitigated when any close drops below the OB low
            if (future["close"] < ob.ob_low).any():
                ob.active = False
        else:
            # Mitigated when any close rises above the OB high
            if (future["close"] > ob.ob_high).any():
                ob.active = False

    return obs
