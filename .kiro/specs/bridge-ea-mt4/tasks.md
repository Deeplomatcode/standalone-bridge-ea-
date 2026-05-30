# Tasks

## Introduction

These are ordered, atomic implementation tasks for Kiro to execute one at a time. Each task maps directly to a function or behaviour defined in `design.md` and traced to a requirement in `requirements.md`. Complete them in sequence — later tasks depend on earlier ones. Show code for each task and wait for approval before proceeding.

## Task List

---

## Phase 1: Scaffold & Configuration ✅ COMPLETE

- [x] **Task 1.1 — Create file and declare inputs**
  - `MQL4/Experts/Bridge_MT4_File.mq4` exists with `#property strict`, version header, and all `extern` input parameters declared correctly.

- [x] **Task 1.2 — Implement `OnInit()`**
  - Logs all three folder paths. Validates trailing backslash with warnings. Calls `EventSetTimer(PollIntervalSeconds)`. Returns `INIT_SUCCEEDED`.

- [x] **Task 1.3 — Implement `OnDeinit()`**
  - Calls `EventKillTimer()`. Logs shutdown message.

> **Kiro: Do not re-implement Phase 1. Open `MQL4/Experts/Bridge_MT4_File.mq4` and append to the existing file from Phase 2 onwards.**

---

## Phase 2: File I/O Utilities

> ⚠️ **`FILE_COMMON` is mandatory on every file operation in this phase.** MQL4 sandboxes I/O to `MQL4\Files\` by default. Without `FILE_COMMON`, absolute paths like `C:\bridge\outgoing\` silently redirect to the MT4 sandbox and nothing will work. See the "MQL4 File I/O — Critical Rules" section in `design.md`.

- [ ] **Task 2.1 — Implement `StringTrim(string s)`**
  - Strip leading and trailing space characters and `\r` from a string.
  - Return the trimmed string.
  - This must be implemented first — every other function in this phase depends on it.

- [ ] **Task 2.2 — Implement `ReadActionFile()`**
  - Signature: `bool ReadActionFile(string path, string &id, string &asset, string &action, string &side, double &size, string &ordertype, double &sl, double &tp, int &magic, string &valid_until, string &comment)`
  - **Open file with `FILE_READ | FILE_TXT | FILE_COMMON`.** Return `false` immediately if `INVALID_HANDLE` (log error; caller skips without archiving).
  - Loop `FileReadString` until `FileIsEnding`.
  - For each line: call `StringSplit(line, '=', parts)`. **If result < 2, skip the line silently** (handles blank lines and lines without `=`).
  - Apply `StringTrim` to key and value; populate matching out-parameters; silently ignore unknown keys.
  - Close file handle before returning `true`.

- [ ] **Task 2.3 — Implement `WriteFeedback()`**
  - Signature: `void WriteFeedback(string id, string asset, string action, string status, string side, double size, string tickets, double avg_price, string message, int error_code)`
  - **Open `FeedbackFolder + id + "_result.txt"` with `FILE_WRITE | FILE_TXT | FILE_COMMON`.**
  - Write all 10 fields as `key=value` lines in this order: `id`, `status`, `asset`, `action`, `side`, `size`, `tickets`, `avg_price`, `message`, `error_code`.
  - Close handle after writing.
  - Log file path if `VerboseLogging`.

- [ ] **Task 2.4 — Implement `ArchiveActionFile(string path)`**
  - Extract filename only from the full path (strip the folder prefix).
  - **Call `FileMove(path, 0, ArchiveFolder + filename, FILE_COMMON | FILE_REWRITE)`.**
  - If move fails, **call `FileDelete(path, FILE_COMMON)`** as fallback.
  - Log outcome if `VerboseLogging`.

---

## Phase 3: Validation

- [ ] **Task 3.1 — Required field validation**
  - In `ProcessActionFile`: after `ReadActionFile`, check `id`, `action`, and `asset` are all non-empty.
  - On failure: `WriteFeedback(REJECTED, MissingRequiredField, error_code=1)` → `ArchiveActionFile` → return.

- [ ] **Task 3.2 — `OnlyCurrentSymbol` filter**
  - If `OnlyCurrentSymbol == true` and `asset != Symbol()`: `WriteFeedback(REJECTED, SymbolMismatch, error_code=1)` → `ArchiveActionFile` → return.

- [ ] **Task 3.3 — Lot size check**
  - For `OPEN` actions: if `size > MaxLotsPerTrade`: `WriteFeedback(REJECTED, LotSizeExceeded, error_code=1)` → return `false`.

- [ ] **Task 3.4 — Spread check**
  - For `OPEN` actions: `spread_points = (Ask - Bid) / Point`. If `spread_points > MaxSpread`: `WriteFeedback(REJECTED, MaxSpreadExceeded, error_code=1)` → return `false`.

- [ ] **Task 3.5 — `valid_until` expiry check**
  - If `valid_until` is non-empty: parse year, month, day, hour, min, sec from the ISO string using `StringSubstr`.
  - Build a `datetime` value and compare with `TimeCurrent()` (broker server time — see timezone rule in `design.md`).
  - If expired: `WriteFeedback(REJECTED, ActionExpired, error_code=1)` → return `false`.
  - If malformed: log warning, treat as valid (fail-open), continue.

- [ ] **Task 3.6 — Assemble `ValidateOpen()` function**
  - Signature: `bool ValidateOpen(string id, string asset, double size, string side, string valid_until)`
  - This function wraps Tasks 3.3, 3.4, and 3.5 into a single named function matching the design signature exactly.
  - Call each check in order: lot size → spread → side validity → expiry.
  - Pass `id` through to `WriteFeedback` calls inside each check.
  - Return `false` on the first failed check; return `true` only if all checks pass.
  - This function does not archive — the caller (`ProcessActionFile`) handles archiving.

---

## Phase 4: OPEN Execution

- [ ] **Task 4.1 — `AskForConfirmation` prompt**
  - This logic lives inside `ExecuteOpen()` as its first step.
  - If `AskForConfirmation == true`: call `MessageBox` with a trade summary string showing asset, side, size, and price.
  - Note: `MessageBox` is a blocking Win32 dialog — the MT4 terminal UI freezes until the user responds. This is intentional.
  - If user clicks Cancel (`IDCANCEL`): `WriteFeedback(REJECTED, UserCancelled, error_code=1)` → return (caller archives).

- [ ] **Task 4.2 — Implement `ExecuteOpen()`**
  - Signature: `void ExecuteOpen(string id, string asset, string side, double size, double sl, double tp, int magic, string comment)`
  - Step 1: If `AskForConfirmation` — show `MessageBox`; on Cancel → `WriteFeedback(REJECTED, UserCancelled)` → return.
  - Step 2: `RefreshRates()`.
  - Step 3: Determine `op`: `OP_BUY` for BUY, `OP_SELL` for SELL.
  - Step 4: Determine `price`: `Ask` for BUY, `Bid` for SELL.
  - Step 5: Determine final magic: `magic > 0 ? magic : MagicNumberBase`.
  - Step 6: Call `OrderSend(asset, op, size, price, Slippage, sl, tp, comment, finalMagic, 0, colour)`.
  - Step 7a — On `ticket > 0`:
    - **Call `OrderSelect(ticket, SELECT_BY_TICKET)` before reading any order properties.**
    - Call `WriteFeedback(FILLED, tickets=IntegerToString(ticket), avg_price=OrderOpenPrice(), error_code=0)`.
  - Step 7b — On failure: `WriteFeedback(REJECTED, error_code=2, message=IntegerToString(GetLastError()))`.

  > ⚠️ `OrderSelect(ticket, SELECT_BY_TICKET)` is mandatory before `OrderOpenPrice()`. After `OrderSend`, the order context is not guaranteed. Skipping this call returns the fill price of whatever order was previously selected, not the new one.

---

## Phase 5: CLOSE_ALL Execution

- [ ] **Task 5.1 — Implement `ExecuteCloseAll()`**
  - Signature: `void ExecuteCloseAll(string id, string asset, int magic)`
  - Determine `resolvedSymbol`: if `OnlyCurrentSymbol == true` → `Symbol()`; else → `asset` from the action file.
  - Loop `i` from `OrdersTotal()-1` downto `0` (reverse to avoid index shifting as orders are removed).
  - `OrderSelect(i, SELECT_BY_POS, MODE_TRADES)`.
  - Filter: `OrderSymbol() == resolvedSymbol` AND (`magic == 0` OR `OrderMagicNumber() == magic`).
  - Determine close price: `Bid` for `OP_BUY`, `Ask` for `OP_SELL`.
  - Call `OrderClose(OrderTicket(), OrderLots(), closePrice, Slippage, colour)`.
  - Accumulate closed ticket strings; track `failCount`.
  - After loop: if `closedCount > 0` → `WriteFeedback(FILLED, tickets=comma-joined list)`.
  - If `closedCount == 0` → `WriteFeedback(REJECTED, NoOrdersToClose, error_code=1)`.

---

## Phase 6: Main Poll Loop

- [ ] **Task 6.1 — Implement `OnTimer()`**
  - Build mask: `BridgeFolder + "*.txt"`.
  - **Call `FileFindFirst(mask, filename, FILE_COMMON)`.**
  - If handle is valid, loop `FileFindNext` calling `ProcessActionFile(BridgeFolder + filename)`.
  - Call `FileFindClose(handle)`.

- [ ] **Task 6.2 — Implement `ProcessActionFile(string path)`**
  - Call `ReadActionFile(path, ...)` → on `false`, log error and return without archiving (retry next cycle).
  - Validate required fields (Task 3.1) → on failure: `WriteFeedback` + archive + return.
  - Check `OnlyCurrentSymbol` (Task 3.2) → on failure: `WriteFeedback` + archive + return.
  - Branch on `action`:
    - `"OPEN"` → call `ValidateOpen(id, asset, size, side, valid_until)` → on `false`, archive + return. On `true`, call `ExecuteOpen(...)` → then `ArchiveActionFile`.
    - `"CLOSE_ALL"` → call `ExecuteCloseAll(id, asset, magic)` → then `ArchiveActionFile`.
    - Other → `WriteFeedback(REJECTED, UnknownAction, error_code=1)` → `ArchiveActionFile`.
  - Every branch must call `ArchiveActionFile` before returning. No file is ever left in `BridgeFolder` after processing.

---

## Phase 7: Compile & Manual Test

- [ ] **Task 7.1 — Compile without errors in MetaEditor**
  - Open MetaEditor, compile `Bridge_MT4_File.mq4`.
  - Resolve all errors. Target: zero errors. Style warnings are acceptable.

- [ ] **Task 7.2 — Manual test: OPEN BUY and OPEN SELL**
  - Create folders: `C:\bridge\outgoing\`, `C:\bridge\incoming\`, `C:\bridge\archive\`.
  - Attach EA to a demo chart (e.g. EURUSD M1).
  - **Test BUY:** drop this file into `outgoing\`:
    ```
    id=test_buy_001
    asset=EURUSD
    action=OPEN
    side=BUY
    size=0.01
    order_type=MARKET
    sl=
    tp=
    comment=kiro_test_buy
    magic_number=
    valid_until=
    ```
  - **Verify:** order appears in MT4 Orders tab. Open `incoming\test_buy_001_result.txt` and confirm: `status=FILLED`, `tickets=<non-zero number>`, `avg_price=<non-zero>`, `error_code=0`. Confirm file moved to `archive\`.
  - **Test SELL:** repeat with `side=SELL` and `id=test_sell_001`.
  - **Verify SELL:** order opened at `Bid`. Feedback confirms `FILLED`.

- [ ] **Task 7.3 — Manual test: CLOSE_ALL**
  - With at least one open order, drop this file into `outgoing\`:
    ```
    id=test_close_001
    asset=EURUSD
    action=CLOSE_ALL
    side=
    size=
    order_type=
    sl=
    tp=
    comment=
    magic_number=
    valid_until=
    ```
  - **Verify:** orders closed in MT4. Open `incoming\test_close_001_result.txt` and confirm: `status=FILLED`, `tickets=<list of closed tickets>`, `error_code=0`. Confirm file moved to `archive\`.

- [ ] **Task 7.4 — Manual test: rejection scenarios**
  - **LotSizeExceeded:** set `size=10.0` with `MaxLotsPerTrade=1.0`. Expect feedback: `status=REJECTED`, `message=LotSizeExceeded`, `error_code=1`.
  - **ActionExpired:** set `valid_until=2020-01-01T00:00:00`. Expect feedback: `status=REJECTED`, `message=ActionExpired`, `error_code=1`.
  - **MissingRequiredField:** omit the `id=` line entirely. Expect feedback: `status=REJECTED`, `message=MissingRequiredField`, `error_code=1`.
  - **SymbolMismatch:** set `OnlyCurrentSymbol=true` in EA inputs. Send an action with `asset=GBPUSD` on a EURUSD chart. Expect feedback: `status=REJECTED`, `message=SymbolMismatch`, `error_code=1`.
  - For all rejection tests: confirm the action file is moved to `archive\` and no trade is opened.

---

## Phase 8: Python Bridge Writer (Separate Spec)

> Tracked in `.kiro/specs/python-bridge-writer/` — begin only after all Phase 7 tests pass.

- [ ] **Task 8.1** — Scaffold `python/bridge/action_writer.py`
- [ ] **Task 8.2** — Implement `write_action_file()` with atomic `os.replace`
- [ ] **Task 8.3** — Implement `write_open_action()` with `ValueError` guards
- [ ] **Task 8.4** — Implement `write_close_all_action()`
- [ ] **Task 8.5** — Unit tests: verify file contents, atomic write, `ValueError` on bad inputs
- [ ] **Task 8.6** — End-to-end test: Python writes file → EA picks up → feedback appears in `incoming\`

---

## Definition of Done

Bridge_MT4_File.mq4 is complete when:
1. Compiles with zero errors in MetaEditor.
2. OPEN BUY and OPEN SELL tests each produce a real order and a `FILLED` feedback file with correct field values.
3. CLOSE_ALL test closes those orders and produces a `FILLED` feedback file with the ticket list.
4. All four rejection scenarios produce `REJECTED` feedback with correct `message` and `error_code=1`. No trades opened. Files archived.
5. No action file is ever left in `outgoing\` after processing.
