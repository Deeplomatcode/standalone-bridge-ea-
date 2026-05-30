# Tasks

## Introduction

Ordered implementation tasks for the Python bridge writer module. Build in sequence — each task depends on the one before it.

## Task List

- [ ] **Task 1** — Create `python/bridge/action_writer.py` with `write_action_file(path, **kwargs)` using atomic temp+rename
- [ ] **Task 2** — Implement `generate_action_id(asset)` → `{asset}_{YYYYMMDD}_{HHMMSS}_{uuid4[:8]}`
- [ ] **Task 3** — Implement `write_open_action(folder, asset, side, size, sl, tp, comment, magic)` with ValueError guards
- [ ] **Task 4** — Implement `write_close_all_action(folder, asset, comment, magic)`
- [ ] **Task 5** — Unit tests: verify file contents, atomic write, ValueError on bad inputs
- [ ] **Task 6** — End-to-end test with live EA: Python drops file → EA executes → feedback appears in `incoming\`
