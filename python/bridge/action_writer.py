"""
python/bridge/action_writer.py

Writes key=value action files for consumption by Bridge_MT4_File.mq4.

File protocol contract:
  - Encoding: UTF-8, newline-separated (\n)
  - All fields written even if blank (blank = empty string after =)
  - No trailing spaces
  - Atomic write: .tmp file written first, then os.replace() to final path
    so the EA never reads a partial file.

Do NOT change field names or encoding — the EA parses these byte-for-byte.
"""

import os
import uuid
from datetime import datetime


def write_action_file(path: str, **kwargs) -> None:
    """Write a key=value action file atomically.

    Writes to a .tmp sibling file first, then renames to *path* using
    os.replace() which is atomic on both Windows (same volume) and POSIX.
    The EA will never observe a partial file.

    Args:
        path:     Full destination path, e.g. r"C:\\bridge\\outgoing\\my_action.txt"
        **kwargs: Field names and values written as key=value lines in
                  insertion order. Values are coerced to str; None becomes "".

    Raises:
        IOError: if the write or rename fails (e.g. folder does not exist).
    """
    tmp_path = path + ".tmp"

    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            for key, value in kwargs.items():
                # Coerce None to empty string; all other values to str
                line_value = "" if value is None else str(value)
                f.write(f"{key}={line_value}\n")
    except OSError as exc:
        # Clean up the partial .tmp if it was created
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise IOError(
            f"write_action_file: failed to write tmp file '{tmp_path}': {exc}"
        ) from exc

    # Atomic rename — on Windows this requires os.replace (not os.rename)
    # os.replace overwrites the destination if it exists (idempotent)
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        raise IOError(
            f"write_action_file: atomic rename failed '{tmp_path}' -> '{path}': {exc}"
        ) from exc


def generate_action_id(asset: str) -> str:
    """Generate a unique action ID for an action file.

    Format: {ASSET}_{YYYYMMDD}_{HHMMSS}_{uuid4[:8]}

    The UUID suffix guarantees uniqueness even when two actions are
    generated within the same second (e.g. rapid-fire signals).

    Uses datetime.utcnow() for the timestamp component. The caller is
    responsible for ensuring valid_until is expressed in broker server
    time — this ID timestamp is for uniqueness only, not for expiry logic.

    Args:
        asset: Symbol string, e.g. "EURUSD" or "XAUUSD". Coerced to
               uppercase. Must be non-empty.

    Returns:
        ID string, e.g. "EURUSD_20260523_142301_a3f8c1d2"

    Raises:
        ValueError: if asset is empty or whitespace-only.
    """
    asset = asset.strip().upper()
    if not asset:
        raise ValueError("generate_action_id: asset must be a non-empty string")

    now = datetime.utcnow()
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M%S")
    uid_part  = uuid.uuid4().hex[:8]

    return f"{asset}_{date_part}_{time_part}_{uid_part}"


def write_open_action(
    folder: str,
    asset: str,
    side: str,
    size: float,
    sl: float = 0.0,
    tp: float = 0.0,
    comment: str = "",
    magic: int = 0,
    valid_until: str = "",
) -> str:
    """Write an OPEN action file to *folder*.

    Validates inputs before writing. Raises ValueError immediately on
    invalid inputs — no file is written on validation failure.

    Args:
        folder:      Destination folder, e.g. r"C:\\bridge\\outgoing\\".
                     Must end with a path separator.
        asset:       Symbol string, e.g. "EURUSD". Coerced to uppercase.
        side:        "BUY" or "SELL" (case-insensitive, normalised to upper).
        size:        Lot size, must be > 0.
        sl:          Stop loss price. 0.0 means no stop loss.
        tp:          Take profit price. 0.0 means no take profit.
        comment:     Order comment string (optional).
        magic:       Magic number override. 0 means use EA default.
        valid_until: Expiry datetime in broker server time, ISO 8601 format
                     e.g. "2026-05-23T14:30:00". Empty string = no expiry.
                     MUST be in broker server time, not UTC or local time.

    Returns:
        Full path of the written action file.

    Raises:
        ValueError: if side is not BUY/SELL, or size <= 0, or asset is empty.
        IOError:    if the file cannot be written (folder missing, permissions).
    """
    # --- Validation (fail fast, no file written on error) ---
    asset_norm = asset.strip().upper()
    if not asset_norm:
        raise ValueError("write_open_action: asset must be a non-empty string")

    side_norm = side.strip().upper()
    if side_norm not in ("BUY", "SELL"):
        raise ValueError(
            f"write_open_action: side must be 'BUY' or 'SELL', got '{side}'"
        )

    if size <= 0:
        raise ValueError(
            f"write_open_action: size must be > 0, got {size}"
        )

    # --- Generate unique ID and build file path ---
    action_id = generate_action_id(asset_norm)
    filename  = f"{action_id}.txt"
    path      = os.path.join(folder, filename)

    # --- Format sl/tp/magic: 0 written as empty string ---
    # EA treats blank sl/tp as 0.0; blank magic_number uses MagicNumberBase
    sl_str    = "" if sl    == 0.0 else str(sl)
    tp_str    = "" if tp    == 0.0 else str(tp)
    magic_str = "" if magic == 0   else str(magic)

    # --- Write atomically via write_action_file ---
    # Use asset.strip() (not asset_norm) to preserve broker-specific casing,
    # e.g. "EURUSDm" on Exness must not be uppercased to "EURUSDM".
    write_action_file(
        path,
        id=action_id,
        asset=asset.strip(),
        action="OPEN",
        side=side_norm,
        size=size,
        order_type="MARKET",
        sl=sl_str,
        tp=tp_str,
        comment=comment,
        magic_number=magic_str,
        valid_until=valid_until,
    )

    return path

def write_close_all_action(
    folder: str,
    asset: str,
    comment: str = "",
    magic: int = 0,
    valid_until: str = "",
) -> str:
    """Write a CLOSE_ALL action file to *folder*.

    Closes all open orders for *asset* matching the optional *magic* filter.
    No size or side validation — CLOSE_ALL does not open positions.

    Args:
        folder:      Destination folder, e.g. r"C:\\bridge\\outgoing\\".
        asset:       Symbol to close, e.g. "EURUSD". Coerced to uppercase.
        comment:     Optional comment string (informational only).
        magic:       If > 0, only orders with this magic number are closed.
                     0 means close all orders regardless of magic number.
        valid_until: Expiry datetime in broker server time, ISO 8601 format.
                     Empty string = no expiry.
                     MUST be in broker server time, not UTC or local time.

    Returns:
        Full path of the written action file.

    Raises:
        ValueError: if asset is empty.
        IOError:    if the file cannot be written (folder missing, permissions).
    """
    # --- Validation ---
    asset_norm = asset.strip().upper()
    if not asset_norm:
        raise ValueError("write_close_all_action: asset must be a non-empty string")

    # --- Generate unique ID and build file path ---
    action_id = generate_action_id(asset_norm)
    filename  = f"{action_id}.txt"
    path      = os.path.join(folder, filename)

    magic_str = "" if magic == 0 else str(magic)

    # --- Write atomically ---
    # Use asset.strip() (not asset_norm) to preserve broker-specific casing.
    # side, size, order_type, sl, tp are blank — EA ignores them for CLOSE_ALL
    write_action_file(
        path,
        id=action_id,
        asset=asset.strip(),
        action="CLOSE_ALL",
        side="",
        size="",
        order_type="",
        sl="",
        tp="",
        comment=comment,
        magic_number=magic_str,
        valid_until=valid_until,
    )

    return path
