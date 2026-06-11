#!/usr/bin/env python3
"""Entry point — Standalone Bridge EA trading system.

Loads config from environment variables, runs preflight checks, then
starts the orchestrator polling loop.

Quick start
-----------
1. Copy .env.example to .env and fill in your values.
2. Start MT4 with Bridge_MT4_File.mq4 attached to a chart.
   Set BridgeFolder  = absolute path to BRIDGE_FOLDER below.
   Set FeedbackFolder = absolute path to FEEDBACK_FOLDER below.
3. From the python/ directory:

       # Option A — export vars manually
       export SYMBOLS=EURUSDm,XAUUSDm
       export ACCOUNT_EQUITY=10000
       python run.py

       # Option B — load from .env (requires python-dotenv)
       pip install python-dotenv
       python -c "from dotenv import load_dotenv; load_dotenv('../.env')" && python run.py

       # Option C — inline
       SYMBOLS=EURUSDm ACCOUNT_EQUITY=10000 python run.py

4. Watch the log output. You should see:
       [EURUSDm] regime=TRENDING_UP  active_obs=3
       Dispatched: EURUSDm BUY 0.20L  SL=1.09500 TP=1.11500  file=bridge/outgoing/...
   Then in MT4 the EA picks up the file and writes a feedback file.
   The orchestrator reads it on the next cycle.

Folder note
-----------
If MT4 runs on Windows and Python runs on macOS/Linux, you need a shared
folder (Dropbox, network share, or mapped drive) that both sides can access.
Point both BRIDGE_FOLDER/FEEDBACK_FOLDER and the EA's BridgeFolder/FeedbackFolder
to the same physical directory.

Environment variables
---------------------
See .env.example for the full list with defaults.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from core.config import TradingConfig
from core.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_level: str = "INFO", log_file: str = "logs/trading.log") -> None:
    """Configure root logger — console + rotating file."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def ensure_dirs(config: TradingConfig) -> None:
    """Create bridge, feedback, and data directories if they do not exist."""
    for folder in (config.bridge_folder, config.feedback_folder, config.data_dir):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def preflight(config: TradingConfig, logger: logging.Logger) -> bool:
    """Log config summary and validate critical settings.

    Returns:
        True if all checks pass. False if any critical value is invalid —
        the caller should abort rather than starting the loop.
    """
    sep = "=" * 64
    logger.info(sep)
    logger.info("  STANDALONE BRIDGE EA — PAPER TRADE MODE")
    logger.info(sep)
    logger.info(f"  symbols           : {', '.join(config.symbols)}")
    logger.info(f"  timeframe         : {config.timeframe}")
    logger.info(f"  bridge_folder     : {config.bridge_folder}")
    logger.info(f"  feedback_folder   : {config.feedback_folder}")
    logger.info(f"  data_dir          : {config.data_dir}")
    logger.info(f"  poll_interval     : {config.poll_interval}s")
    logger.info(f"  session_filter    : {config.session_filter_enabled}")
    logger.info(f"  account_equity    : ${config.account_equity:,.2f}")
    logger.info(f"  risk_pct          : {config.risk_pct_per_trade}%  "
                f"(${config.account_equity * config.risk_pct_per_trade / 100:,.2f} / trade)")
    logger.info(f"  contract_size     : {config.contract_size:,.0f}")
    logger.info(f"  max_open_trades   : {config.max_open_trades}")
    logger.info(f"  max_lot_per_sym   : {config.max_lot_per_symbol}")
    logger.info(f"  rr_ratio          : {config.rr_ratio}")
    logger.info(f"  adx_threshold     : {config.adx_trend_threshold}")
    logger.info(sep)

    errors: list[str] = []

    if not config.symbols:
        errors.append("SYMBOLS is empty — set at least one symbol")
    if config.account_equity <= 0:
        errors.append(f"ACCOUNT_EQUITY must be > 0, got {config.account_equity}")
    if not (0 < config.risk_pct_per_trade <= 10):
        errors.append(
            f"RISK_PCT_PER_TRADE must be in (0, 10], got {config.risk_pct_per_trade}"
        )
    if config.contract_size <= 0:
        errors.append(f"CONTRACT_SIZE must be > 0, got {config.contract_size}")
    if config.poll_interval < 1:
        errors.append(f"POLL_INTERVAL must be >= 1s, got {config.poll_interval}")

    for err in errors:
        logger.error(f"  PREFLIGHT FAIL: {err}")

    if errors:
        logger.error(sep)
        logger.error("  Aborting — fix the above before restarting.")
        logger.error(sep)
        return False

    logger.info("  Preflight passed — starting orchestrator.")
    logger.info(sep)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO")
    log_file  = os.environ.get("LOG_FILE",  "logs/trading.log")

    setup_logging(log_level, log_file)
    logger = logging.getLogger("run")

    config = TradingConfig.from_env()
    ensure_dirs(config)

    if not preflight(config, logger):
        sys.exit(1)

    orchestrator = Orchestrator(config)
    orchestrator.run()   # blocks — exits on KeyboardInterrupt


if __name__ == "__main__":
    main()
