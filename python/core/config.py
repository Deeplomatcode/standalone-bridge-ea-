"""
python/core/config.py

Runtime configuration for the trading platform orchestrator.
All fields have sensible defaults. Use TradingConfig.from_env() to
override from environment variables in production.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class TradingConfig:
    """Central configuration for the orchestrator run loop.

    Every field can be overridden via environment variable using from_env().
    """
    symbols:             List[str] = field(default_factory=lambda: ["EURUSDm", "XAUUSDm"])
    timeframe:           str   = "H1"
    bridge_folder:       str   = "bridge/outgoing"
    feedback_folder:     str   = "bridge/incoming"
    data_dir:            str   = "data/csv"
    poll_interval:       int   = 60
    feedback_timeout:    int   = 90
    lookback_days:       int   = 30
    max_open_trades:     int   = 5
    max_lot_per_symbol:  float = 0.10
    max_lot_total:       float = 0.50
    max_drawdown_pct:    float = 10.0
    lot_size:            float = 0.01
    sl_buffer:           float = 0.0002
    rr_ratio:            float = 2.0
    adx_trend_threshold:   float = 25.0
    session_filter_enabled: bool = True   # Phase 18: restrict to London/NY killzones
    # Phase 19: risk-based lot sizing
    risk_pct_per_trade: float = 1.0       # % of equity risked per trade (e.g. 1.0 = 1 %)
    account_equity:     float = 10_000.0  # account balance in USD
    contract_size:      float = 100_000.0 # units per lot (FX=100000, XAUUSD=100)

    @classmethod
    def from_env(cls) -> "TradingConfig":
        """Construct config from environment variables, falling back to defaults."""
        default = cls()
        raw_symbols = os.environ.get("SYMBOLS", "")
        symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()] or default.symbols
        return cls(
            symbols=symbols,
            timeframe=os.environ.get("TIMEFRAME", default.timeframe),
            bridge_folder=os.environ.get("BRIDGE_FOLDER", default.bridge_folder),
            feedback_folder=os.environ.get("FEEDBACK_FOLDER", default.feedback_folder),
            data_dir=os.environ.get("DATA_DIR", default.data_dir),
            poll_interval=int(os.environ.get("POLL_INTERVAL", default.poll_interval)),
            feedback_timeout=int(os.environ.get("FEEDBACK_TIMEOUT", default.feedback_timeout)),
            lookback_days=int(os.environ.get("LOOKBACK_DAYS", default.lookback_days)),
            max_open_trades=int(os.environ.get("MAX_OPEN_TRADES", default.max_open_trades)),
            max_lot_per_symbol=float(os.environ.get("MAX_LOT_PER_SYMBOL", default.max_lot_per_symbol)),
            max_lot_total=float(os.environ.get("MAX_LOT_TOTAL", default.max_lot_total)),
            max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", default.max_drawdown_pct)),
            lot_size=float(os.environ.get("LOT_SIZE", default.lot_size)),
            sl_buffer=float(os.environ.get("SL_BUFFER", default.sl_buffer)),
            rr_ratio=float(os.environ.get("RR_RATIO", default.rr_ratio)),
            adx_trend_threshold=float(os.environ.get("ADX_TREND_THRESHOLD", default.adx_trend_threshold)),
            session_filter_enabled=os.environ.get("SESSION_FILTER_ENABLED", "true").lower() != "false",
            risk_pct_per_trade=float(os.environ.get("RISK_PCT_PER_TRADE", default.risk_pct_per_trade)),
            account_equity=float(os.environ.get("ACCOUNT_EQUITY", default.account_equity)),
            contract_size=float(os.environ.get("CONTRACT_SIZE", default.contract_size)),
        )
