"""
python/tests/test_webhook_parser.py

Unit tests for webhook/parser.py — Phase 14.

No Flask, no filesystem — pure function tests only.
"""

import pytest

from webhook.parser import WebhookPayload, parse_payload, validate_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_payload(**kwargs):
    base = {
        "token":  "secret",
        "action": "OPEN",
        "symbol": "EURUSDm",
        "side":   "BUY",
        "size":   0.01,
    }
    base.update(kwargs)
    return base


def _close_payload(**kwargs):
    base = {"token": "secret", "action": "CLOSE_ALL", "symbol": "EURUSDm"}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# WebhookPayload dataclass
# ---------------------------------------------------------------------------

class TestWebhookPayload:

    def test_required_fields_set(self):
        p = WebhookPayload(action="OPEN", symbol="EURUSDm")
        assert p.action == "OPEN"
        assert p.symbol == "EURUSDm"

    def test_optional_defaults(self):
        p = WebhookPayload(action="OPEN", symbol="EURUSDm")
        assert p.side    == ""
        assert p.size    == 0.01
        assert p.comment == ""
        assert p.token   == ""


# ---------------------------------------------------------------------------
# parse_payload — happy paths
# ---------------------------------------------------------------------------

class TestParsePayloadSuccess:

    def test_open_buy_returns_payload(self):
        p = parse_payload(_open_payload())
        assert p.action == "OPEN"
        assert p.side   == "BUY"
        assert p.symbol == "EURUSDm"
        assert p.size   == pytest.approx(0.01)

    def test_open_sell_accepted(self):
        p = parse_payload(_open_payload(side="SELL"))
        assert p.side == "SELL"

    def test_close_all_returns_payload(self):
        p = parse_payload(_close_payload())
        assert p.action == "CLOSE_ALL"
        assert p.symbol == "EURUSDm"

    def test_action_case_insensitive(self):
        p = parse_payload(_open_payload(action="open"))
        assert p.action == "OPEN"

    def test_side_case_insensitive(self):
        p = parse_payload(_open_payload(side="sell"))
        assert p.side == "SELL"

    def test_symbol_whitespace_stripped(self):
        p = parse_payload(_open_payload(symbol="  EURUSDm  "))
        assert p.symbol == "EURUSDm"

    def test_comment_optional_defaults_to_empty(self):
        p = parse_payload(_open_payload())
        assert p.comment == ""

    def test_comment_passed_through(self):
        p = parse_payload(_open_payload(comment="OB H1 retest"))
        assert p.comment == "OB H1 retest"

    def test_token_passed_through(self):
        p = parse_payload(_open_payload(token="abc123"))
        assert p.token == "abc123"

    def test_close_all_does_not_require_side_or_size(self):
        data = {"action": "CLOSE_ALL", "symbol": "EURUSDm"}
        p    = parse_payload(data)
        assert p.action == "CLOSE_ALL"

    def test_float_size_accepted(self):
        p = parse_payload(_open_payload(size=0.10))
        assert p.size == pytest.approx(0.10)

    def test_integer_size_accepted(self):
        p = parse_payload(_open_payload(size=1))
        assert p.size == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# parse_payload — invalid action
# ---------------------------------------------------------------------------

class TestParsePayloadInvalidAction:

    def test_missing_action_raises(self):
        data = _open_payload()
        del data["action"]
        with pytest.raises(ValueError, match="action"):
            parse_payload(data)

    def test_invalid_action_string_raises(self):
        with pytest.raises(ValueError, match="action"):
            parse_payload(_open_payload(action="MODIFY"))

    def test_empty_action_raises(self):
        with pytest.raises(ValueError, match="action"):
            parse_payload(_open_payload(action=""))


# ---------------------------------------------------------------------------
# parse_payload — invalid symbol
# ---------------------------------------------------------------------------

class TestParsePayloadInvalidSymbol:

    def test_missing_symbol_raises(self):
        data = _open_payload()
        del data["symbol"]
        with pytest.raises(ValueError, match="symbol"):
            parse_payload(data)

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol"):
            parse_payload(_open_payload(symbol=""))

    def test_whitespace_only_symbol_raises(self):
        with pytest.raises(ValueError, match="symbol"):
            parse_payload(_open_payload(symbol="   "))


# ---------------------------------------------------------------------------
# parse_payload — invalid side (OPEN only)
# ---------------------------------------------------------------------------

class TestParsePayloadInvalidSide:

    def test_missing_side_raises_for_open(self):
        data = _open_payload()
        del data["side"]
        with pytest.raises(ValueError, match="side"):
            parse_payload(data)

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError, match="side"):
            parse_payload(_open_payload(side="HOLD"))

    def test_empty_side_raises(self):
        with pytest.raises(ValueError, match="side"):
            parse_payload(_open_payload(side=""))


# ---------------------------------------------------------------------------
# parse_payload — invalid size (OPEN only)
# ---------------------------------------------------------------------------

class TestParsePayloadInvalidSize:

    def test_zero_size_raises(self):
        with pytest.raises(ValueError, match="size"):
            parse_payload(_open_payload(size=0))

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match="size"):
            parse_payload(_open_payload(size=-0.01))

    def test_non_numeric_size_raises(self):
        with pytest.raises(ValueError, match="size"):
            parse_payload(_open_payload(size="big"))


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------

class TestValidateToken:

    def test_matching_tokens_true(self):
        assert validate_token("abc123", "abc123") is True

    def test_mismatched_tokens_false(self):
        assert validate_token("wrong", "abc123") is False

    def test_empty_payload_token_false(self):
        assert validate_token("", "abc123") is False

    def test_empty_server_secret_always_false(self):
        """Empty WEBHOOK_TOKEN must always reject — no unprotected deployments."""
        assert validate_token("anything", "") is False

    def test_both_empty_false(self):
        assert validate_token("", "") is False

    def test_case_sensitive(self):
        assert validate_token("Secret", "secret") is False
