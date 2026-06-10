"""
python/tests/test_webhook_app.py

Flask test-client tests for webhook/app.py — Phase 14.

write_open_action and write_close_all_action are mocked so no filesystem
or live MT4 path is required.
"""

import pytest
from unittest.mock import patch

from webhook.app import app


TOKEN     = "test-secret-token"
FAKE_PATH = "/tmp/bridge/EURUSDm_20260101_120000_abcd1234.txt"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("BRIDGE_FOLDER", "/tmp/bridge")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _open_payload(**kwargs):
    base = {
        "token":  TOKEN,
        "action": "OPEN",
        "symbol": "EURUSDm",
        "side":   "BUY",
        "size":   0.01,
    }
    base.update(kwargs)
    return base


def _close_payload(**kwargs):
    base = {"token": TOKEN, "action": "CLOSE_ALL", "symbol": "EURUSDm"}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Successful dispatch
# ---------------------------------------------------------------------------

class TestWebhookSuccess:

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_buy_returns_200(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload())
        assert rv.status_code == 200

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_buy_response_status_ok(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload())
        assert rv.get_json()["status"] == "ok"

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_buy_response_contains_action(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload())
        assert rv.get_json()["action"] == "OPEN"

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_buy_response_contains_symbol(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload(symbol="XAUUSDm"))
        assert rv.get_json()["symbol"] == "XAUUSDm"

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_buy_calls_write_open_action_with_correct_args(self, mock_write, client):
        client.post("/webhook", json=_open_payload(symbol="EURUSDm", side="BUY", size=0.05))
        kwargs = mock_write.call_args.kwargs
        assert kwargs["asset"] == "EURUSDm"
        assert kwargs["side"]  == "BUY"
        assert kwargs["size"]  == pytest.approx(0.05)

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_open_sell_returns_200(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload(side="SELL"))
        assert rv.status_code == 200

    @patch("webhook.app.write_close_all_action", return_value=FAKE_PATH)
    def test_close_all_returns_200(self, mock_write, client):
        rv = client.post("/webhook", json=_close_payload())
        assert rv.status_code == 200

    @patch("webhook.app.write_close_all_action", return_value=FAKE_PATH)
    def test_close_all_calls_write_close_all_with_correct_asset(self, mock_write, client):
        client.post("/webhook", json=_close_payload(symbol="XAUUSDm"))
        kwargs = mock_write.call_args.kwargs
        assert kwargs["asset"] == "XAUUSDm"

    @patch("webhook.app.write_open_action", return_value=FAKE_PATH)
    def test_response_file_is_basename_not_full_path(self, mock_write, client):
        rv   = client.post("/webhook", json=_open_payload())
        fname = rv.get_json()["file"]
        assert "/" not in fname and "\\" not in fname


# ---------------------------------------------------------------------------
# Authentication failures
# ---------------------------------------------------------------------------

class TestWebhookAuth:

    def test_wrong_token_returns_401(self, client):
        rv = client.post("/webhook", json=_open_payload(token="wrong"))
        assert rv.status_code == 401

    def test_missing_token_returns_401(self, client):
        data = _open_payload()
        del data["token"]
        rv = client.post("/webhook", json=data)
        assert rv.status_code == 401

    def test_empty_token_returns_401(self, client):
        rv = client.post("/webhook", json=_open_payload(token=""))
        assert rv.status_code == 401

    def test_auth_failure_response_body(self, client):
        rv = client.post("/webhook", json=_open_payload(token="bad"))
        assert rv.get_json()["status"] == "error"
        assert "unauthorized" in rv.get_json()["message"].lower()


# ---------------------------------------------------------------------------
# Bad request failures
# ---------------------------------------------------------------------------

class TestWebhookBadRequest:

    def test_invalid_json_returns_400(self, client):
        rv = client.post("/webhook", data="not-json",
                         content_type="application/json")
        assert rv.status_code == 400

    def test_empty_body_returns_400(self, client):
        rv = client.post("/webhook", data="",
                         content_type="application/json")
        assert rv.status_code == 400

    def test_invalid_action_returns_400(self, client):
        rv = client.post("/webhook", json=_open_payload(action="MODIFY"))
        assert rv.status_code == 400

    def test_missing_symbol_returns_400(self, client):
        data = _open_payload()
        del data["symbol"]
        rv = client.post("/webhook", json=data)
        assert rv.status_code == 400

    def test_invalid_side_returns_400(self, client):
        rv = client.post("/webhook", json=_open_payload(side="HOLD"))
        assert rv.status_code == 400

    def test_zero_size_returns_400(self, client):
        rv = client.post("/webhook", json=_open_payload(size=0))
        assert rv.status_code == 400

    def test_negative_size_returns_400(self, client):
        rv = client.post("/webhook", json=_open_payload(size=-0.01))
        assert rv.status_code == 400

    def test_error_response_contains_status_and_message(self, client):
        rv   = client.post("/webhook", json=_open_payload(action="MODIFY"))
        body = rv.get_json()
        assert body["status"]  == "error"
        assert "message" in body


# ---------------------------------------------------------------------------
# IO error path
# ---------------------------------------------------------------------------

class TestWebhookIOError:

    @patch("webhook.app.write_open_action", side_effect=OSError("disk full"))
    def test_io_error_returns_500(self, mock_write, client):
        rv = client.post("/webhook", json=_open_payload())
        assert rv.status_code == 500

    @patch("webhook.app.write_open_action", side_effect=OSError("disk full"))
    def test_io_error_response_body(self, mock_write, client):
        rv   = client.post("/webhook", json=_open_payload())
        body = rv.get_json()
        assert body["status"]  == "error"
        assert "IO error" in body["message"]
