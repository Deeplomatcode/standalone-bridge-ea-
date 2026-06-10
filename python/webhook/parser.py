"""
python/webhook/parser.py

TradingView webhook payload parser — Phase 14.

Pure functions — no Flask imports, no file I/O.
Designed to be tested in isolation from the HTTP layer.

Expected TradingView alert message (configure in TV alert → Message field):

  OPEN:
    {
      "token":   "your-secret",
      "action":  "OPEN",
      "symbol":  "EURUSDm",
      "side":    "BUY",
      "size":    0.01,
      "comment": "OB retest H1"
    }

  CLOSE_ALL:
    {
      "token":  "your-secret",
      "action": "CLOSE_ALL",
      "symbol": "EURUSDm"
    }

Action and side values are normalised to uppercase before validation,
so "open", "Open", "buy", "Buy" are all accepted.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any, Dict


VALID_ACTIONS: frozenset = frozenset({"OPEN", "CLOSE_ALL"})
VALID_SIDES:   frozenset = frozenset({"BUY", "SELL"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class WebhookPayload:
    """Parsed and validated TradingView alert payload.

    Attributes:
        action:  "OPEN" or "CLOSE_ALL".
        symbol:  Broker symbol, e.g. "EURUSDm". Passed through as-is.
        side:    "BUY" or "SELL". Required for OPEN; ignored for CLOSE_ALL.
        size:    Lot size. Required for OPEN; ignored for CLOSE_ALL.
        comment: Optional trade comment / strategy label.
        token:   Raw token from the payload (validated before parsing).
    """
    action:  str
    symbol:  str
    side:    str   = ""
    size:    float = 0.01
    comment: str   = ""
    token:   str   = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_payload(data: Dict[str, Any]) -> WebhookPayload:
    """Parse and validate an incoming webhook payload.

    Normalises action and side to uppercase. Strips whitespace from symbol.

    Args:
        data: Dict parsed from the JSON request body.

    Returns:
        A validated WebhookPayload.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    # --- action (required, all variants) ---
    action = str(data.get("action", "")).strip().upper()
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"invalid action '{action}'. Must be one of {sorted(VALID_ACTIONS)}"
        )

    # --- symbol (required, all variants) ---
    symbol = str(data.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("missing required field: symbol")

    payload = WebhookPayload(
        action  = action,
        symbol  = symbol,
        comment = str(data.get("comment", "")),
        token   = str(data.get("token",   "")),
    )

    # --- side + size (required for OPEN only) ---
    if action == "OPEN":
        side = str(data.get("side", "")).strip().upper()
        if side not in VALID_SIDES:
            raise ValueError(
                f"invalid side '{side}'. Must be one of {sorted(VALID_SIDES)}"
            )
        payload.side = side

        try:
            size = float(data.get("size", 0))
        except (TypeError, ValueError):
            raise ValueError("size must be a positive number")

        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")

        payload.size = size

    return payload


def validate_token(payload_token: str, expected_token: str) -> bool:
    """Constant-time comparison between the request token and the server secret.

    Uses hmac.compare_digest to prevent timing attacks.

    Args:
        payload_token:  Token from the incoming request payload.
        expected_token: Server-side secret (from WEBHOOK_TOKEN env var).

    Returns:
        True only if both tokens are non-empty and identical.
        Always returns False if the server secret is empty — no blank-password
        deployments.
    """
    if not expected_token:
        return False
    return hmac.compare_digest(
        payload_token.encode("utf-8"),
        expected_token.encode("utf-8"),
    )
