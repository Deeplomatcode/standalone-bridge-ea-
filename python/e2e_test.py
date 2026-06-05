"""
python/e2e_test.py

Task 6 — End-to-end live test: Python writes action file → EA executes →
feedback file appears in FeedbackFolder.

Usage (run from the python/ directory on Windows):
    python e2e_test.py

Prerequisites:
  1. Bridge_MT4_File.mq4 is attached to a live demo chart in MT4
  2. The three bridge folders exist:
       bridge\outgoing\   (BridgeFolder)
       bridge\incoming\   (FeedbackFolder)
       bridge\archive\    (ArchiveFolder)
  3. Python 3.8+ installed

What this script does:
  1. Writes an OPEN BUY action file to bridge\outgoing\ using write_open_action
  2. Polls bridge\incoming\ for up to 10 seconds waiting for the feedback file
  3. Reads and prints the feedback file contents
  4. Asserts status=FILLED and error_code=0
  5. Writes a CLOSE_ALL action file
  6. Polls for the CLOSE_ALL feedback file
  7. Asserts status=FILLED
  8. Reports PASS or FAIL with details
"""

import os
import sys
import time
from typing import Optional

# Allow running from the python/ directory without installing the package
sys.path.insert(0, os.path.dirname(__file__))

from bridge.action_writer import write_open_action, write_close_all_action

# ---------------------------------------------------------------------------
# Configuration — adjust to match EA input parameters
# ---------------------------------------------------------------------------
BRIDGE_FOLDER   = os.path.join("bridge", "outgoing")   # relative to MT4 MQL4\Files\
FEEDBACK_FOLDER = os.path.join("bridge", "incoming")   # relative to MT4 MQL4\Files\
ASSET           = "EURUSD"
SIZE            = 0.01
POLL_TIMEOUT    = 10   # seconds to wait for feedback
POLL_INTERVAL   = 0.5  # seconds between checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_fields(path: str) -> dict:
    """Parse a key=value feedback file into a dict."""
    fields = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value
    return fields


def wait_for_feedback(action_id: str, timeout: float) -> Optional[dict]:
    """Poll FeedbackFolder for {action_id}_result.txt. Returns fields dict or None."""
    feedback_path = os.path.join(FEEDBACK_FOLDER, f"{action_id}_result.txt")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(feedback_path):
            return read_fields(feedback_path)
        time.sleep(POLL_INTERVAL)
    return None


def assert_field(fields: dict, key: str, expected: str, label: str):
    actual = fields.get(key, "<missing>")
    if actual != expected:
        print(f"  FAIL [{label}] {key}: expected '{expected}', got '{actual}'")
        return False
    print(f"  OK   [{label}] {key}={actual}")
    return True


# ---------------------------------------------------------------------------
# Test 1 — OPEN BUY
# ---------------------------------------------------------------------------

def test_open_buy() -> Optional[str]:
    """Write OPEN BUY, wait for FILLED feedback. Returns action_id or None on failure."""
    print("\n--- Test 1: OPEN BUY ---")
    path = write_open_action(
        BRIDGE_FOLDER,
        asset=ASSET,
        side="BUY",
        size=SIZE,
        comment="e2e_test_buy",
    )
    action_id = os.path.splitext(os.path.basename(path))[0]
    print(f"  Action file written: {path}")
    print(f"  Action ID: {action_id}")
    print(f"  Waiting up to {POLL_TIMEOUT}s for feedback...")

    fields = wait_for_feedback(action_id, POLL_TIMEOUT)
    if fields is None:
        print(f"  FAIL: feedback file not found after {POLL_TIMEOUT}s")
        return None

    print(f"  Feedback received:")
    for k, v in fields.items():
        print(f"    {k}={v}")

    ok = True
    ok &= assert_field(fields, "status",     "FILLED", "OPEN BUY")
    ok &= assert_field(fields, "error_code", "0",      "OPEN BUY")

    ticket = fields.get("tickets", "")
    if not ticket:
        print("  FAIL [OPEN BUY] tickets: expected non-empty, got ''")
        ok = False
    else:
        print(f"  OK   [OPEN BUY] tickets={ticket}")

    avg_price = fields.get("avg_price", "0.0")
    if float(avg_price) <= 0:
        print(f"  FAIL [OPEN BUY] avg_price: expected > 0, got '{avg_price}'")
        ok = False
    else:
        print(f"  OK   [OPEN BUY] avg_price={avg_price}")

    return action_id if ok else None


# ---------------------------------------------------------------------------
# Test 2 — CLOSE_ALL
# ---------------------------------------------------------------------------

def test_close_all() -> bool:
    """Write CLOSE_ALL, wait for FILLED feedback. Returns True on success."""
    print("\n--- Test 2: CLOSE_ALL ---")
    path = write_close_all_action(
        BRIDGE_FOLDER,
        asset=ASSET,
        comment="e2e_test_close",
    )
    action_id = os.path.splitext(os.path.basename(path))[0]
    print(f"  Action file written: {path}")
    print(f"  Action ID: {action_id}")
    print(f"  Waiting up to {POLL_TIMEOUT}s for feedback...")

    fields = wait_for_feedback(action_id, POLL_TIMEOUT)
    if fields is None:
        print(f"  FAIL: feedback file not found after {POLL_TIMEOUT}s")
        return False

    print(f"  Feedback received:")
    for k, v in fields.items():
        print(f"    {k}={v}")

    ok = True
    ok &= assert_field(fields, "status",     "FILLED", "CLOSE_ALL")
    ok &= assert_field(fields, "error_code", "0",      "CLOSE_ALL")

    tickets = fields.get("tickets", "")
    if not tickets:
        print("  FAIL [CLOSE_ALL] tickets: expected non-empty, got ''")
        ok = False
    else:
        print(f"  OK   [CLOSE_ALL] tickets={tickets}")

    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Bridge EA — End-to-End Live Test (Task 6)")
    print("=" * 60)
    print(f"BridgeFolder:   {os.path.abspath(BRIDGE_FOLDER)}")
    print(f"FeedbackFolder: {os.path.abspath(FEEDBACK_FOLDER)}")
    print(f"Asset:          {ASSET}")
    print(f"Size:           {SIZE} lots")

    # Verify folders exist before starting
    for folder, label in [(BRIDGE_FOLDER, "BridgeFolder"),
                          (FEEDBACK_FOLDER, "FeedbackFolder")]:
        if not os.path.isdir(folder):
            print(f"\nERROR: {label} not found: {os.path.abspath(folder)}")
            print("  Create the folder and ensure it matches the EA's BridgeFolder path.")
            sys.exit(1)

    results = {}

    # Test 1: OPEN BUY
    open_id = test_open_buy()
    results["OPEN BUY"] = open_id is not None

    if open_id is None:
        print("\nSkipping CLOSE_ALL — OPEN BUY did not produce a ticket.")
        results["CLOSE_ALL"] = False
    else:
        # Small delay to ensure the order is registered before closing
        time.sleep(1.0)
        results["CLOSE_ALL"] = test_close_all()

    # Summary
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    all_passed = True
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {test_name}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("ALL TESTS PASSED — Task 6 complete.")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED — check output above.")
        sys.exit(1)
