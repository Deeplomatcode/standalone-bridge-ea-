"""
python/bridge/feedback_reader.py

Reads and parses feedback files written by Bridge_MT4_File.mq4.

File protocol contract (mirror of action_writer.py):
  - Encoding: UTF-8, newline-separated (\\n)
  - All fields present even if blank (blank = empty string after =)
  - Written atomically by the EA — no partial reads possible
  - Filename: <action_id>_result.txt

Feedback file format (key=value):
  id=<action_id>
  status=FILLED|REJECTED|ERROR
  asset=<symbol>
  action=OPEN|CLOSE_ALL
  side=BUY|SELL|
  size=<float>
  tickets=<ticket>[,<ticket>...]
  avg_price=<float>
  message=<string>
  error_code=<int>

Do NOT change field names or encoding — the EA writes these byte-for-byte.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRecord:
    """Parsed representation of a single EA feedback file.

    All fields have safe defaults so callers never get AttributeError even
    when the EA writes a partial record (e.g. a REJECTED with minimal fields).
    """
    id: str          = ""
    status: str      = ""     # "FILLED", "REJECTED", "ERROR"
    asset: str       = ""
    action: str      = ""     # "OPEN", "CLOSE_ALL"
    side: str        = ""     # "BUY", "SELL", or "" for CLOSE_ALL
    size: float      = 0.0    # lot size; 0.0 for CLOSE_ALL feedback
    tickets: List[str] = field(default_factory=list)  # order ticket numbers
    avg_price: float = 0.0    # average fill price; 0.0 when not applicable
    message: str     = ""     # error description or empty on success
    error_code: int  = 0      # 0 = success, non-zero = MT4 error code

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_filled(self) -> bool:
        """True when the EA successfully executed the action."""
        return self.status == "FILLED"

    @property
    def is_rejected(self) -> bool:
        """True when the EA rejected the action (validation or broker error)."""
        return self.status == "REJECTED"

    @property
    def is_error(self) -> bool:
        """True when the EA encountered an unexpected internal error."""
        return self.status == "ERROR"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_raw_fields(path: str) -> dict:
    """Read a key=value file into a plain dict.

    - Encoding: UTF-8
    - Lines without '=' are silently skipped
    - Value is everything after the first '=' on the line (preserves '=' in values)
    - Keys are stripped of whitespace; values are NOT stripped (preserve broker strings)
    """
    fields: dict = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value
    return fields


def _safe_float(value: str, default: float = 0.0) -> float:
    """Convert string to float; return *default* on blank or parse error."""
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _safe_int(value: str, default: int = 0) -> int:
    """Convert string to int; return *default* on blank or parse error."""
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_tickets(raw: str) -> List[str]:
    """Parse comma-separated ticket list.  '371932593,371929691' -> ['371932593', '371929691']."""
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_feedback_file(path: str) -> FeedbackRecord:
    """Parse a feedback file written by Bridge_MT4_File.mq4.

    Args:
        path: Full path to the *_result.txt file, e.g.
              r"C:\\bridge\\incoming\\EURUSD_20260523_142301_a3f8c1d2_result.txt"

    Returns:
        FeedbackRecord with all fields populated. Missing or blank fields
        use safe defaults (empty string / 0.0 / 0 / []).

    Raises:
        IOError:    if the file cannot be opened or read.
        ValueError: if the file contains no key=value lines at all.
    """
    try:
        raw = _parse_raw_fields(path)
    except OSError as exc:
        raise IOError(
            f"parse_feedback_file: cannot read '{path}': {exc}"
        ) from exc

    if not raw:
        raise ValueError(
            f"parse_feedback_file: no key=value lines found in '{path}'"
        )

    return FeedbackRecord(
        id=raw.get("id", ""),
        status=raw.get("status", ""),
        asset=raw.get("asset", ""),
        action=raw.get("action", ""),
        side=raw.get("side", ""),
        size=_safe_float(raw.get("size", "")),
        tickets=_parse_tickets(raw.get("tickets", "")),
        avg_price=_safe_float(raw.get("avg_price", "")),
        message=raw.get("message", ""),
        error_code=_safe_int(raw.get("error_code", "")),
    )


def poll_for_feedback(
    folder: str,
    action_id: str,
    timeout: float,
    interval: float = 0.5,
) -> Optional[FeedbackRecord]:
    """Poll *folder* for <action_id>_result.txt until found or *timeout* expires.

    Uses time.monotonic() so clock adjustments don't affect the deadline.

    Args:
        folder:    FeedbackFolder path, e.g. r"C:\\bridge\\incoming\\".
        action_id: The action ID from the written action file, e.g.
                   "EURUSD_20260523_142301_a3f8c1d2".
        timeout:   Maximum seconds to wait (float, e.g. 90.0).
        interval:  Seconds between filesystem checks. Default 0.5s.
                   Set lower for faster response; set higher to reduce I/O.

    Returns:
        Parsed FeedbackRecord if the file appears within *timeout*.
        None if the deadline is reached without finding the file.
    """
    filename = f"{action_id}_result.txt"
    path = os.path.join(folder, filename)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if os.path.isfile(path):
            return parse_feedback_file(path)
        time.sleep(interval)

    return None
