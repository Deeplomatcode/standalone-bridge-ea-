# Requirements Document

## Introduction

Bridge_MT4_File.mq4 is a standalone Expert Advisor for MetaTrader 4 that turns MT4 into a generic file-driven order executor. It periodically scans a folder on disk for action files created by an external application (or manually), parses each action, validates it against configurable risk constraints, executes it using MT4 trade functions, and writes a feedback file so the external application can track fills and errors.

It has no dependency on Python or any external runtime. It can be tested entirely with manually created text files, and later connected to a Python trading engine without any changes to the EA.

## Requirements

### Requirement 1: Periodic File Polling

**User Story:** As a trading system operator, I want the EA to automatically scan for new action files at a configurable interval, so that trade instructions are picked up and executed without manual intervention.

#### Acceptance Criteria

1. EA sets a repeating timer in `OnInit` using `EventSetTimer(PollIntervalSeconds)`
2. On every tick, `OnTimer` lists all `*.txt` files in `BridgeFolder` using `FileFindFirst` / `FileFindNext`
3. Timer is killed in `OnDeinit` via `EventKillTimer`
4. Poll interval is configurable via `PollIntervalSeconds` (default: 1 second)

---

### Requirement 2: Action File Parsing

**User Story:** As a trading system operator, I want the EA to parse action files in a simple key=value format, so that external applications can easily generate trade instructions without complex serialization.

#### Acceptance Criteria

1. File is opened with `FILE_READ | FILE_TXT`; successful file opening with the correct flags satisfies this criterion regardless of subsequent parsing outcome
2. Each line split on `=` using `StringSplit`
3. Keys and values trimmed of whitespace and `\r` via a custom `StringTrim` helper
4. Required fields parsed: `id`, `asset`, `action`, `side`, `size`, `order_type`, `sl`, `tp`, `magic_number`, `valid_until`, `comment`
5. Unknown keys are silently ignored
6. IF the file cannot be opened, THEN the EA SHALL log the error and skip the file, retrying on the next poll cycle without archiving it

---

### Requirement 3: Required Field Validation

**User Story:** As a trading system operator, I want the EA to reject action files missing required fields, so that incomplete instructions never result in unintended trades.

#### Acceptance Criteria

1. WHEN an action file is missing `id`, `action`, or `asset` fields or they are empty, THEN the EA SHALL write `REJECTED` feedback with `message=MissingRequiredField` and `error_code=1`
2. The action file is archived or deleted after rejection
3. No trade is executed for a rejected action

---

### Requirement 4: Symbol Filtering

**User Story:** As a trading system operator, I want to optionally restrict the EA to only process actions for the current chart symbol, so that I can run multiple EA instances on different symbols without cross-execution.

#### Acceptance Criteria

1. WHEN `OnlyCurrentSymbol = true` AND the `asset` field does not match the chart symbol, THEN the EA SHALL write `REJECTED` feedback with `message=SymbolMismatch` and `error_code=1`
2. WHEN `OnlyCurrentSymbol = false` (default), any valid symbol in the file is used without filtering

---

### Requirement 5: Lot Size Validation

**User Story:** As a trading system operator, I want the EA to enforce a maximum lot size per trade, so that risk limits are respected and oversized positions are never opened.

#### Acceptance Criteria

1. WHEN an `OPEN` action has `size > MaxLotsPerTrade`, THEN the EA SHALL write `REJECTED` feedback with `message=LotSizeExceeded` and `error_code=1`
2. No trade is executed when lot size validation fails

---

### Requirement 6: Spread Validation

**User Story:** As a trading system operator, I want the EA to reject trades when the spread is too wide, so that orders are not filled at unfavorable prices during high-volatility periods.

#### Acceptance Criteria

1. WHEN an `OPEN` action is processed AND the current spread in points exceeds `MaxSpread`, THEN the EA SHALL write `REJECTED` feedback with `message=MaxSpreadExceeded` and `error_code=1`
2. Spread is calculated as `(Ask - Bid) / Point`

---

### Requirement 7: Time Validity Check

**User Story:** As a trading system operator, I want action files to expire after a specified time, so that stale trade instructions are never executed if they were delayed or queued.

#### Acceptance Criteria

1. WHEN a `valid_until` field is present in ISO 8601 format AND the current server time is past that value, THEN the EA SHALL write `REJECTED` feedback with `message=ActionExpired` and `error_code=1`
2. Year, month, day, hour, minute, second are parsed from the ISO string and compared with `TimeCurrent()`
3. IF `valid_until` is blank, no time check is applied
4. IF the date string is malformed, the EA SHALL log a warning and treat the action as valid (fail-open)

---

### Requirement 8: Confirmation Prompt

**User Story:** As a manual trader, I want the option to confirm each trade before execution, so that I can review and approve automated trade instructions before they are sent to the broker.

#### Acceptance Criteria

1. WHEN `AskForConfirmation = true`, THEN the EA SHALL display a `MessageBox` with a trade summary before executing any trade
2. IF the user cancels the dialog, THEN the EA SHALL write `REJECTED` feedback with `message=UserCancelled` and `error_code=1`, and archive the file
3. IF the user confirms the dialog, THEN the EA SHALL proceed with trade execution

---

### Requirement 9: OPEN Market Order Execution

**User Story:** As a trading system operator, I want the EA to execute market orders based on action file instructions, so that the external application can open positions on MT4 without direct broker API access.

#### Acceptance Criteria

1. WHEN `action=OPEN` AND `order_type=MARKET` AND all validations pass, THEN the EA SHALL call `RefreshRates()` before sending the order
2. WHEN `side=BUY`, THEN the EA SHALL call `OrderSend` with `OP_BUY` at `Ask`
3. WHEN `side=SELL`, THEN the EA SHALL call `OrderSend` with `OP_SELL` at `Bid`
4. `sl` and `tp` are passed if non-zero, otherwise `0.0`
5. Magic number used is `magic_number` from file if > 0, else `MagicNumberBase`
6. WHEN `OrderSend` returns a ticket > 0, THEN the EA SHALL write `FILLED` feedback with the ticket number and `OrderOpenPrice()`
7. WHEN `OrderSend` fails (ticket <= 0), THEN the EA SHALL write `REJECTED` feedback with `error_code=2` and the `GetLastError()` value in `message`

---

### Requirement 10: CLOSE_ALL Action

**User Story:** As a trading system operator, I want the EA to close all open orders for a symbol on command, so that positions can be exited quickly without specifying individual ticket numbers.

#### Acceptance Criteria

1. WHEN `action=CLOSE_ALL`, THEN the EA SHALL iterate open orders from `OrdersTotal()-1` downto `0` (reverse order to avoid index shifting)
2. Orders are filtered by symbol, respecting the `OnlyCurrentSymbol` setting
3. IF `magic_number` is provided, THEN only orders with that magic number are closed
4. `OP_BUY` orders are closed at `Bid`; `OP_SELL` orders are closed at `Ask`
5. WHEN orders are successfully closed, THEN the EA SHALL write `FILLED` feedback with a comma-separated list of closed ticket numbers
6. WHEN no matching orders are found, THEN the EA SHALL write `REJECTED` feedback with `message=NoOrdersToClose`

---

### Requirement 11: Feedback File Writing

**User Story:** As a trading system operator, I want the EA to write a feedback file for every processed action, so that the external application can track execution outcomes and handle errors programmatically.

#### Acceptance Criteria

1. AFTER every action (success or rejection), THEN the EA SHALL write a feedback file to `FeedbackFolder`
2. Filename format is `{id}_result.txt`
3. Fields written: `id`, `status`, `asset`, `action`, `side`, `size`, `tickets`, `avg_price`, `message`, `error_code`
4. File opened with `FILE_WRITE | FILE_TXT`
5. File handle is closed after writing

---

### Requirement 12: Action File Archiving

**User Story:** As a trading system operator, I want processed action files to be removed from the watch folder, so that the EA does not reprocess the same instruction on the next poll cycle.

#### Acceptance Criteria

1. AFTER every processed action file (any outcome), THEN the EA SHALL attempt `FileMove(path, ArchiveFolder + filename)` first
2. IF the move fails, THEN the EA SHALL use `FileDelete(path)` as a fallback
3. The file must be removed before `ProcessActionFile` returns

---

### Requirement 13: Initialization Logging

**User Story:** As a trading system operator, I want the EA to log its configuration on startup, so that I can verify the correct paths and settings are active without inspecting input parameters manually.

#### Acceptance Criteria

1. `OnInit` logs `BridgeFolder`, `FeedbackFolder`, and `ArchiveFolder` to the Experts tab
2. WHEN any configured path does not end with `\\`, THEN the EA SHALL log a warning
3. Timer is started via `EventSetTimer(PollIntervalSeconds)` during initialization
4. `OnInit` returns `INIT_SUCCEEDED`

---

### Requirement 14: Clean Shutdown

**User Story:** As a trading system operator, I want the EA to shut down cleanly when removed or when the terminal closes, so that no resources are leaked and no partial operations are left in an inconsistent state.

#### Acceptance Criteria

1. `EventKillTimer()` is called in `OnDeinit` as the sole cleanup mechanism for the timer; no alternative cleanup path is used
2. A shutdown message is logged on exit
3. No dangling file handles remain after shutdown

---

### Requirement 15: Verbose Logging

**User Story:** As a trading system operator, I want to control the verbosity of EA logging, so that I can get detailed diagnostic output during development and testing while keeping logs clean in production.

#### Acceptance Criteria

1. WHEN `VerboseLogging = true`, THEN the EA SHALL log every significant step including: file found, file parsed, validation outcome, execution outcome, feedback written, and file archived
2. WHEN `VerboseLogging = false`, THEN the EA SHALL only log errors and warnings

---

## Out of Scope (v1)

- Limit or stop orders (`order_type` other than `MARKET`)
- Partial closes
- Trailing stop management
- Python runtime dependency
- Network connectivity of any kind
- MT5 support

## Glossary

| Term | Definition |
|------|------------|
| Action file | A `key=value` plain text file dropped into `BridgeFolder` that instructs the EA to execute a trade |
| Feedback file | A `key=value` plain text file written by the EA to `FeedbackFolder` confirming the outcome of an action |
| BridgeFolder | The directory the EA watches for incoming action files (default: `C:\bridge\outgoing\`) |
| FeedbackFolder | The directory the EA writes feedback files to (default: `C:\bridge\incoming\`) |
| ArchiveFolder | The directory the EA moves processed action files to (default: `C:\bridge\archive\`) |
| OPEN | Action type instructing the EA to open a new market order |
| CLOSE_ALL | Action type instructing the EA to close all open orders for the specified symbol |
| FILLED | Feedback status meaning the action was executed successfully |
| REJECTED | Feedback status meaning the action was not executed (validation failure or MT4 error) |
| MagicNumberBase | Default magic number assigned to orders when none is specified in the action file |
| error_code 0 | Success |
| error_code 1 | Local validation rejection (spread, lot size, symbol, expiry, user cancel) |
| error_code 2 | MT4 `OrderSend` / `OrderClose` error — `GetLastError()` value in `message` field |
