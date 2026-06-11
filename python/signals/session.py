"""Phase 18 — Session / killzone filter.

Restricts signal entry to the highest-probability trading windows:
  - London Open:   07:00–10:00 UTC
  - New York AM:   13:00–16:00 UTC

Filters are pure functions — no side effects, fully testable in isolation.
Both the orchestrator and the backtester apply the filter after signal
generation, before risk gating.

All timestamps are assumed to be UTC (broker server time for most brokers).
If your broker uses a different timezone, convert before calling these functions.
"""
from __future__ import annotations

from datetime import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from signals.strategy import TradeSignal


# ---------------------------------------------------------------------------
# Default killzone definitions
# ---------------------------------------------------------------------------

#: Default killzones: {name: (start_time_utc, end_time_utc)}.
#: End time is exclusive — a signal at exactly 10:00 UTC is NOT in London Open.
DEFAULT_KILLZONES: Dict[str, Tuple[time, time]] = {
    "london_open": (time(7, 0),  time(10, 0)),
    "new_york_am": (time(13, 0), time(16, 0)),
}


# ---------------------------------------------------------------------------
# Pure filter functions
# ---------------------------------------------------------------------------

def is_in_killzone(
    ts: pd.Timestamp,
    killzones: Optional[Dict[str, Tuple[time, time]]] = None,
) -> bool:
    """Return True if *ts* falls within any configured killzone window.

    Args:
        ts:         Signal or bar timestamp. Must be UTC-aligned or
                    explicitly tz-aware UTC.
        killzones:  Dict mapping name → (start, end) time pairs.
                    Defaults to ``DEFAULT_KILLZONES``.

    Returns:
        True if the hour:minute of *ts* is within at least one window.

    Examples:
        >>> is_in_killzone(pd.Timestamp("2025-01-06 08:30:00"))
        True   # inside London Open
        >>> is_in_killzone(pd.Timestamp("2025-01-06 11:00:00"))
        False  # between sessions
    """
    if killzones is None:
        killzones = DEFAULT_KILLZONES
    t = ts.time()
    return any(start <= t < end for start, end in killzones.values())


def filter_by_session(
    signals: List[TradeSignal],
    killzones: Optional[Dict[str, Tuple[time, time]]] = None,
) -> List[TradeSignal]:
    """Keep only signals whose timestamp falls within a killzone.

    Args:
        signals:    List of TradeSignal objects from generate_signals().
        killzones:  Killzone config. Defaults to ``DEFAULT_KILLZONES``.

    Returns:
        Filtered list — signals outside all killzones are dropped.
        Returns an empty list if *signals* is empty or none pass the filter.
    """
    return [s for s in signals if is_in_killzone(s.timestamp, killzones)]


def active_session_name(
    ts: pd.Timestamp,
    killzones: Optional[Dict[str, Tuple[time, time]]] = None,
) -> Optional[str]:
    """Return the name of the active killzone at *ts*, or None.

    If *ts* falls in multiple windows (unlikely with default config),
    returns the first match in dict iteration order.

    Args:
        ts:         Timestamp to check.
        killzones:  Killzone config. Defaults to ``DEFAULT_KILLZONES``.

    Returns:
        Killzone name string, e.g. "london_open", or None if outside all windows.
    """
    if killzones is None:
        killzones = DEFAULT_KILLZONES
    t = ts.time()
    for name, (start, end) in killzones.items():
        if start <= t < end:
            return name
    return None
