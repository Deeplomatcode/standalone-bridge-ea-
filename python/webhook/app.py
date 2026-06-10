"""
python/webhook/app.py

TradingView webhook receiver — Phase 14.

Exposes a single POST /webhook endpoint that:
  1. Validates the bearer token against WEBHOOK_TOKEN env var
  2. Parses and validates the JSON payload via webhook.parser
  3. Dispatches to write_open_action or write_close_all_action
  4. Returns a JSON status response

Environment variables:
  WEBHOOK_TOKEN   Required. Secret token embedded in every TV alert.
  BRIDGE_FOLDER   Path to the EA bridge outgoing folder.
                  Default: bridge/outgoing (relative — set an absolute path
                  matching your MT4 machine layout in production).
  PORT            HTTP port. Default: 5000.

TradingView alert message templates (paste into TV alert → Message field):

  OPEN BUY:
    {"token":"{{strategy.order.alert_message}}","action":"OPEN","symbol":"EURUSDm","side":"BUY","size":0.01}

  OPEN SELL:
    {"token":"{{strategy.order.alert_message}}","action":"OPEN","symbol":"EURUSDm","side":"SELL","size":0.01}

  CLOSE ALL:
    {"token":"{{strategy.order.alert_message}}","action":"CLOSE_ALL","symbol":"EURUSDm"}

  Or hardcode the token:
    {"token":"my-secret","action":"OPEN","symbol":"EURUSDm","side":"BUY","size":0.01}

Usage:
  # Dev:
  WEBHOOK_TOKEN=mysecret BRIDGE_FOLDER=/path/to/bridge/outgoing python webhook/app.py

  # Production (gunicorn):
  WEBHOOK_TOKEN=mysecret BRIDGE_FOLDER=/path/to/bridge/outgoing gunicorn webhook.app:app
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request

from bridge.action_writer import write_close_all_action, write_open_action
from webhook.parser import parse_payload, validate_token

log = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _bridge_folder() -> str:
    return os.environ.get("BRIDGE_FOLDER", os.path.join("bridge", "outgoing"))


def _webhook_token() -> str:
    return os.environ.get("WEBHOOK_TOKEN", "")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive a TradingView alert and dispatch to the MT4 bridge.

    Request body: JSON (see module docstring for format).

    Responses:
      200  {"status":"ok",    "action":..., "symbol":..., "file":...}
      400  {"status":"error", "message":...}   — bad payload
      401  {"status":"error", "message":"unauthorized"}   — wrong/missing token
      500  {"status":"error", "message":"IO error: ..."}  — bridge write failed
    """
    # Parse body
    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify({"status": "error", "message": "invalid or missing JSON body"}), 400

    # Token gate
    if not validate_token(str(data.get("token", "")), _webhook_token()):
        log.warning("webhook: unauthorized request from %s", request.remote_addr)
        return jsonify({"status": "error", "message": "unauthorized"}), 401

    # Payload validation
    try:
        payload = parse_payload(data)
    except ValueError as exc:
        log.warning("webhook: bad payload — %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 400

    # Bridge dispatch
    bridge = _bridge_folder()
    try:
        if payload.action == "OPEN":
            path = write_open_action(
                folder  = bridge,
                asset   = payload.symbol,
                side    = payload.side,
                size    = payload.size,
                comment = payload.comment,
            )
        else:   # CLOSE_ALL
            path = write_close_all_action(
                folder = bridge,
                asset  = payload.symbol,
            )
    except OSError as exc:
        log.error("webhook: failed to write action file — %s", exc)
        return jsonify({"status": "error", "message": f"IO error: {exc}"}), 500

    log.info("webhook: wrote %s", path)
    return jsonify({
        "status": "ok",
        "action": payload.action,
        "symbol": payload.symbol,
        "file":   os.path.basename(path),
    }), 200


# ---------------------------------------------------------------------------
# Dev entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
