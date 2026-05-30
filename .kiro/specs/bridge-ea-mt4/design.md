# Design

## Introduction

Bridge_MT4_File.mq4 is implemented as a single self-contained MQL4 file with no external dependencies. It uses MT4's built-in file I/O, timer, and order management functions. The design follows a simple poll-parse-validate-execute-feedback-archive loop.

## Architecture Summary

The EA is a single `.mq4` file with no external dependencies. It uses MT4's built-in file I/O, timer, and order functions. The design is deliberately simple: a poll loop reads files, validates, executes, writes feedback, and archives. The Python engine (future) writes the same files — the EA never knows or cares who created them.

```
[External Producer]          [Bridge_MT4_File.mq4]          [Feedback Consumer]
  Python engine   ──write──▶  BridgeFolder/*.txt  ──read──▶  OnTimer() poll
  Manual .txt     ──write──▶                                  ├── parse
                                                              ├── validate
                                                              ├── execute
                                                              ├── write ──▶ FeedbackFolder/{id}_result.txt
                                                              └── archive ──▶ ArchiveFolder/{filename}
```

---

## Input Parameters

```mql4
extern string BridgeFolder        = "C:\\bridge\\outgoing\\";
extern string FeedbackFolder      = "C:\\bridge\\incoming\\";
extern string ArchiveFolder       = "C:\\bridge\\archive\\";
extern bool   OnlyCurrentSymbol   = false;
extern bool   AskForConfirmation  = false;
extern double MaxLotsPerTrade     = 1.0;
extern double MaxSpread           = 3.0;      // in points
extern int    MagicNumberBase     = 202600;
extern int    PollIntervalSeconds = 1;
extern int    Slippage            = 3;        // in points
extern bool   VerboseLogging      = true;
```

All paths must end with `\\`. The EA logs them on init so you can verify they're correct before any files are processed.

---

## File Protocol

### Action File

- **Location**: `BridgeFolder`
- **Naming convention**: `{asset}_{YYYYMMDD}_{HHMMSS}_{seq}.txt`
  Example: `xauusd_20260523_120001_001.txt`
- **Format**: `key=value`, one pair per line, UTF-8 plain text

| Key | Required | Description |
|-----|----------|-------------|
| `id` | Yes | Unique action identifier (matches filename without extension) |
| `asset` | Yes | Symbol, e.g. `XAUUSD` |
| `action` | Yes | `OPEN` or `CLOSE_ALL` |
| `side` | OPEN only | `BUY` or `SELL` |
| `size` | OPEN only | Lot size, e.g. `0.10` |
| `order_type` | OPEN only | `MARKET` (only supported type in v1) |
| `sl` | No | Stop loss price; blank = 0.0 |
| `tp` | No | Take profit price; blank = 0.0 |
| `magic_number` | No | Override EA magic; blank = use `MagicNumberBase` |
| `valid_until` | No | Datetime in **broker server time** (same timezone as `TimeCurrent()`); blank = always valid. **Do not use UTC or local machine time.** |
| `comment` | No | Order comment string |

> **Timezone rule:** `valid_until` must always be expressed in broker server time — the same timezone returned by `TimeCurrent()`. `TimeCurrent()` returns the broker's last known tick time, not UTC and not the local machine clock. The Python action writer must convert to broker server time before writing this field.

### Feedback File

- **Location**: `FeedbackFolder`
- **Naming**: `{id}_result.txt`
- **Format**: same `key=value` structure

| Key | Description |
|-----|-------------|
| `id` | Mirrors action `id` |
| `status` | `FILLED` or `REJECTED` |
| `asset` | Symbol |
| `action` | Mirrors action type |
| `side` | Mirrors side |
| `size` | Mirrors size |
| `tickets` | Comma-separated ticket numbers (blank if rejected) |
| `avg_price` | Fill price (blank if rejected) |
| `message` | Human-readable outcome description |
| `error_code` | `0` = success, `1` = local risk rejection, `2` = MT4 error |

---

## MQL4 File I/O — Critical Rules

> **All file operations must include the `FILE_COMMON` flag.**

MQL4 sandboxes file I/O to `MQL4\Files\` by default. To use absolute paths like `C:\bridge\outgoing\`, every `FileOpen`, `FileMove`, `FileDelete`, and `FileFindFirst` call must include `FILE_COMMON`. Without this flag, all operations silently redirect to the MT4 sandbox and no bridge files will be found or written.

Correct pattern:
```mql4
int handle = FileOpen(path, FILE_READ | FILE_TXT | FILE_COMMON);
FileFindFirst(mask, name, FILE_COMMON);
FileMove(src, 0, dst, FILE_COMMON | FILE_REWRITE);
FileDelete(path, FILE_COMMON);
```

---

## Module Breakdown

### `OnInit()`
1. Warn if any folder path does not end with `\\`.
2. Log `BridgeFolder`, `FeedbackFolder`, `ArchiveFolder`.
3. Call `EventSetTimer(PollIntervalSeconds)`.
4. Return `INIT_SUCCEEDED`.

### `OnDeinit(const int reason)`
1. Call `EventKillTimer()` — only here, no fallback mechanism.
2. Log shutdown message.

> **Re-entrancy note:** MT4's `OnTimer` is not re-entrant. If a poll cycle takes longer than `PollIntervalSeconds`, the next tick is queued and fires only after the current one completes. This is safe — no mutex logic needed.

### `OnTimer()`
1. Build search mask: `BridgeFolder + "*.txt"`.
2. Use `FileFindFirst(mask, filename, FILE_COMMON)` / `FileFindNext` to iterate matching files.
3. For each file, call `ProcessActionFile(BridgeFolder + filename)`.
4. Call `FileFindClose(handle)`.

### `ProcessActionFile(string path)`
1. Call `ReadActionFile(path, ...)` — on `false`, log error and skip (do not archive; retry next cycle).
2. Validate required fields (`id`, `action`, `asset`) — on failure: `WriteFeedback(REJECTED, MissingRequiredField)` → `ArchiveActionFile`.
3. Check `OnlyCurrentSymbol` filter — on failure: `WriteFeedback(REJECTED, SymbolMismatch)` → `ArchiveActionFile`.
4. Branch on `action`:
   - `"OPEN"` → call `ValidateOpen(...)` then `ExecuteOpen(...)`
   - `"CLOSE_ALL"` → call `ExecuteCloseAll(...)`
   - Unknown → `WriteFeedback(REJECTED, UnknownAction)` → `ArchiveActionFile`.
5. `ArchiveActionFile(path)` is called in every branch before returning.

### `ReadActionFile(string path, string &id, string &asset, string &action, string &side, double &size, string &ordertype, double &sl, double &tp, int &magic, string &valid_until, string &comment) → bool`
- Opens file with `FILE_READ | FILE_TXT | FILE_COMMON`.
- Returns `false` immediately if handle is `INVALID_HANDLE` (log error, caller skips without archiving).
- Loop `FileReadString` until `FileIsEnding`.
- For each line: call `StringSplit(line, '=', parts)`.
  - **If `StringSplit` returns < 2, skip the line silently** (handles blank lines and lines without `=`).
  - Key = `StringTrim(parts[0])`, Value = `StringTrim(parts[1])`.
- Populate matching out-parameters; silently ignore unknown keys.
- Close file handle before returning `true`.

> **MQL4 Note:** `StringSplit` splits on a single `=` character. If a value contains `=` (e.g. a comment like `"OB=TREND"`), only the text before the second `=` is captured. This is a known v1 limitation — acceptable because field values in this protocol do not intentionally contain `=`.

> **Double zero check:** `sl` and `tp` parsed from blank strings via `StrToDouble("")` return exactly `0.0`. Comparison with `== 0.0` is safe in this case.

### `ValidateOpen(string asset, double size, string side, string valid_until) → bool`
- Check `size <= MaxLotsPerTrade` → on fail: `WriteFeedback(REJECTED, LotSizeExceeded, error_code=1)`, return `false`.
- Check spread: `(Ask - Bid) / Point <= MaxSpread` → on fail: `WriteFeedback(REJECTED, MaxSpreadExceeded, error_code=1)`, return `false`.
- Check `side` is `"BUY"` or `"SELL"` → on fail: `WriteFeedback(REJECTED, InvalidSide, error_code=1)`, return `false`.
- If `valid_until` is non-empty:
  - Parse year, month, day, hour, minute, second from the ISO string using `StringSubstr`.
  - Build a `datetime` value and compare with `TimeCurrent()` (broker server time).
  - If expired: `WriteFeedback(REJECTED, ActionExpired, error_code=1)`, return `false`.
  - If malformed: log warning, treat as valid (fail-open), continue.
- Return `true` if all checks pass.

> **Timezone:** `TimeCurrent()` returns broker server time. `valid_until` must be in the same timezone. See File Protocol timezone rule above.

### `ExecuteOpen(string id, string asset, string side, double size, double sl, double tp, int magic, string comment)`
1. If `AskForConfirmation`: show `MessageBox` with trade summary.
   - **Note:** `MessageBox` is a blocking Win32 dialog — the MT4 terminal UI freezes until the user responds. This is intentional behaviour, not a bug.
   - On Cancel (`IDCANCEL`): `WriteFeedback(REJECTED, UserCancelled, error_code=1)`, return.
2. Call `RefreshRates()`.
3. Determine `op`: `OP_BUY` for BUY, `OP_SELL` for SELL.
4. Determine `price`: `Ask` for BUY, `Bid` for SELL.
5. Determine final magic: `magic > 0 ? magic : MagicNumberBase`.
6. Call `OrderSend(asset, op, size, price, Slippage, sl, tp, comment, finalMagic, 0, colour)`.
7. If `ticket > 0`:
   - Call `OrderSelect(ticket, SELECT_BY_TICKET)` to select the filled order.
   - Call `WriteFeedback(FILLED, ticket, OrderOpenPrice(), error_code=0)`.
8. Else: `WriteFeedback(REJECTED, error_code=2, message=IntegerToString(GetLastError()))`.

> **`OrderSelect` required:** `OrderOpenPrice()` reads from the currently selected order context. After `OrderSend`, the selection is not guaranteed. Always call `OrderSelect(ticket, SELECT_BY_TICKET)` before reading order properties.

### `ExecuteCloseAll(string id, string asset, int magic)`
- Determine `resolvedSymbol`: if `OnlyCurrentSymbol == true`, use `Symbol()`; otherwise use `asset` from the action file.
- Loop `i` from `OrdersTotal()-1` downto `0` (reverse to avoid index shifting as orders are closed).
- `OrderSelect(i, SELECT_BY_POS, MODE_TRADES)`.
- Filter: `OrderSymbol() == resolvedSymbol` AND (`magic == 0` OR `OrderMagicNumber() == magic`).
- Determine close price: `Bid` for `OP_BUY`, `Ask` for `OP_SELL`.
- Call `OrderClose(OrderTicket(), OrderLots(), closePrice, Slippage, colour)`.
- Accumulate successfully closed ticket IDs; track `failCount`.
- After loop:
  - If `closedCount > 0`: `WriteFeedback(FILLED, tickets=comma-joined list)`.
  - If `closedCount == 0`: `WriteFeedback(REJECTED, NoOrdersToClose, error_code=1)`.

### `WriteFeedback(string id, string asset, string action, string status, string side, double size, string tickets, double avg_price, string message, int error_code)`
- Opens `FeedbackFolder + id + "_result.txt"` with `FILE_WRITE | FILE_TXT | FILE_COMMON`.
- Writes all 10 fields as `key=value` lines.
- Closes handle.
- Logs path if `VerboseLogging`.

### `ArchiveActionFile(string path)`
- Extracts filename only from full path (strip folder prefix).
- Calls `FileMove(path, 0, ArchiveFolder + filename, FILE_COMMON | FILE_REWRITE)`.
- If move fails, calls `FileDelete(path, FILE_COMMON)` as fallback.
- Logs outcome if `VerboseLogging`.

> **Same-drive guarantee:** `BridgeFolder` and `ArchiveFolder` are both under `C:\bridge\` by default — same volume. `FileMove` on the same volume is atomic. Cross-drive risk does not apply to this configuration.

### `StringTrim(string s) → string`
- Manual helper: strip leading and trailing space characters and `\r` from a string.
- Required because MQL4 has no built-in trim function.
- Must be implemented before any other function that uses it.

---

## VerboseLogging Behaviour

When `VerboseLogging = true`, the EA logs at every significant step:
- File found in BridgeFolder
- File parsed successfully (log `id`, `action`, `asset`)
- Each validation outcome (pass or rejection reason)
- OrderSend result (ticket or error code)
- Feedback file written (log path)
- Action file archived or deleted (log destination)

When `VerboseLogging = false`, only errors and warnings are logged (file open failures, unexpected errors, path warnings on init).

---

## Error Code Reference

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Local validation rejection (spread, lot size, symbol, expiry, user cancel, unknown action) |
| 2 | MT4 `OrderSend` / `OrderClose` error — `GetLastError()` value in `message` field |

---

## Folder Layout (MT4 Host Machine)

```
C:\bridge\
  outgoing\      ← Python / manual drops action files here (BridgeFolder)
  incoming\      ← EA writes feedback files here (FeedbackFolder)
  archive\       ← EA moves processed action files here (ArchiveFolder)
```

All three folders must exist before the EA is attached to a chart. The EA does not create them. MT4 must have write access to all three paths. All file operations use `FILE_COMMON` to access these absolute paths outside the MT4 sandbox.

---

## Sequence Diagram

```
OnTimer()
  │
  ├─ FileFindFirst("BridgeFolder/*.txt", FILE_COMMON)
  │   │
  │   └─ for each file:
  │       │
  │       ├─ ReadActionFile() ──────── FILE_COMMON, skip-if-no-equals, StringTrim
  │       │       └─ FAIL (can't open) → log, skip, retry next cycle
  │       │
  │       ├─ Validate required fields (id, action, asset)
  │       │       └─ FAIL → WriteFeedback(REJECTED) → ArchiveActionFile() → return
  │       │
  │       ├─ OnlyCurrentSymbol check
  │       │       └─ FAIL → WriteFeedback(REJECTED, SymbolMismatch) → Archive → return
  │       │
  │       ├─ ValidateOpen (lot, spread, side, valid_until) — OPEN only
  │       │       └─ FAIL → WriteFeedback(REJECTED) → Archive → return
  │       │
  │       ├─ [AskForConfirmation?] MessageBox — blocks UI until response
  │       │       └─ Cancel → WriteFeedback(REJECTED, UserCancelled) → Archive → return
  │       │
  │       ├─ ExecuteOpen / ExecuteCloseAll
  │       │       ├─ SUCCESS → OrderSelect(ticket) → WriteFeedback(FILLED) → Archive
  │       │       └─ FAIL    → WriteFeedback(REJECTED, error_code=2) → Archive
  │       │
  │       └─ (archive called inside each branch, not at end of loop)
  │
  └─ FileFindClose(handle)
```

---

## Future Extension Points

| Feature | How to Add |
|---------|-----------|
| `CLOSE_WINNERS` / `CLOSE_LOSERS` | Add `action` branch in `ProcessActionFile`, filter by `OrderProfit()` |
| Limit/Stop orders | Extend `ExecuteOpen` to handle `OP_BUYLIMIT`, `OP_SELLSTOP`, etc. |
| Trailing stops | Add `OnTick` handler or separate trailing EA |
| TradingView webhooks | Python webhook receiver writes same action files; EA unchanged |
| MT5 port | Rewrite with `CTrade` — file protocol identical |
| Partial close | Add `CLOSE_PARTIAL` action with `size` field |
