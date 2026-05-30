# Design

## Introduction

Technical design for `python/bridge/action_writer.py` — the Python module that writes action files consumed by `Bridge_MT4_File.mq4`.

## Module Location
`python/bridge/action_writer.py`

## Dependencies
- Python 3.8+
- Standard library only: `os`, `uuid`, `datetime`

---

## File Protocol Contract
The output files must be byte-for-byte compatible with what `Bridge_MT4_File.mq4` expects.
Any change to field names, field order, or file encoding here breaks the EA without warning.

### Output Format
```
id={asset}_{YYYYMMDD}_{HHMMSS}_{uuid4[:8]}
asset={ASSET}
action=OPEN|CLOSE_ALL
side=BUY|SELL|
size=0.10|
order_type=MARKET|
sl=2380.50|
tp=2405.00|
comment={string}
magic_number={int}|
valid_until={ISO8601}|
```
- Encoding: UTF-8, newline-separated (`\n`)
- All fields present even if blank (blank = empty string after `=`)
- No trailing spaces

---

## Functions

### `generate_action_id(asset: str) -> str`
Returns: `{asset.upper()}_{YYYYMMDD}_{HHMMSS}_{uuid4[:8]}`
Uses `datetime.utcnow()` for timestamp.

### `write_action_file(path: str, **kwargs) -> None`
- Writes `.tmp` file first, then `os.replace(tmp, path)` atomically.
- Raises `IOError` if write fails.
- `kwargs` are written as `key=value` lines in insertion order.

### `write_open_action(folder, asset, side, size, sl="", tp="", comment="", magic="", valid_until="") -> str`
- Validates: `side` in `["BUY", "SELL"]`, `size > 0`.
- Raises `ValueError` on invalid inputs.
- Generates `id`, constructs full path, calls `write_action_file`.
- Returns the full path of the written file.

### `write_close_all_action(folder, asset, comment="", magic="", valid_until="") -> str`
- No size/side validation needed.
- Generates `id`, writes CLOSE_ALL action file.
- Returns the full path of the written file.

---

## Error Handling
| Condition | Behaviour |
|-----------|-----------|
| `side` not BUY/SELL | `ValueError` before any write |
| `size <= 0` | `ValueError` before any write |
| Folder does not exist | `IOError` from `os.replace` |
| Partial write (crash mid-write) | `.tmp` file left behind — EA never sees it |

---

## Future Extensions
- `write_close_partial_action()` — once EA supports `CLOSE_PARTIAL`
- `feedback_reader.py` — reads `{id}_result.txt` from `FeedbackFolder`
- `action_router.py` — receives TradingView webhook JSON and calls these helpers
