# Requirements

## Introduction

A lightweight Python module (`action_writer.py`) that writes `key=value` action files for consumption by `Bridge_MT4_File.mq4`. Uses atomic writes (temp file + rename) to prevent the EA from reading a partial file.

## Requirements

---

## User Stories

### 1. Atomic File Write

**Given** a trade action is ready to send,  
**When** `write_action_file(path, **kwargs)` is called,  
**Then** the module writes a `.tmp` file first, then renames it to the final path atomically — the EA never sees a partial file.

### 2. OPEN Action Helper

**Given** a strategy signals a new trade,  
**When** `write_open_action(folder, asset, side, size, sl, tp, comment, magic)` is called,  
**Then** it generates a timestamped unique filename and writes a correctly formatted action file with all required OPEN fields.

### 3. CLOSE_ALL Action Helper

**Given** a risk event or daily limit is hit,  
**When** `write_close_all_action(folder, asset, comment, magic)` is called,  
**Then** it writes a CLOSE_ALL action file with the correct fields and a unique `id`.

### 4. Field Validation

**Given** a caller passes an invalid lot size (≤ 0) or missing required fields,  
**When** the helper is called,  
**Then** it raises a `ValueError` before writing any file — fail fast, don't write invalid actions.

### 5. Unique ID Generation

**Given** multiple actions may be written in quick succession,  
**When** any action helper generates an `id`,  
**Then** the `id` is guaranteed unique using `{asset}_{YYYYMMDD}_{HHMMSS}_{uuid4_short}` format.

---

## Out of Scope (v1)
- Reading feedback files (handled by a separate `feedback_reader.py`)
- Network transport
- Retry logic
- Strategy or signal logic
