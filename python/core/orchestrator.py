"""
python/core/orchestrator.py

Main run loop — ties OHLCV ingestion, regime classification, order block
detection, signal generation, risk gating, and bridge dispatch into a
continuously running trading system.

Phase 15 implementation. Position book (Phase 16) will populate
open_positions; for now it's always [].
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Set

from data.ohlcv import update_ohlcv
from signals.regime import classify_regime
from signals.order_blocks import detect_order_blocks, mark_mitigated
from signals.strategy import generate_signals, execute_signals, TradeSignal
from risk.manager import RiskManager, Position
from core.config import TradingConfig

logger = logging.getLogger(__name__)


class Orchestrator:
    """Main orchestrator — runs the full trading pipeline in a polling loop."""

    def __init__(self, config: TradingConfig):
        self.config = config
        self.risk_manager = RiskManager(
            max_open_trades=config.max_open_trades,
            max_lot_per_symbol=config.max_lot_per_symbol,
            max_lot_total=config.max_lot_total,
            max_drawdown_pct=config.max_drawdown_pct,
        )
        self.open_positions: List[Position] = []   # Phase 16 will populate this
        self._dispatched_ids: Set[str] = set()     # dedup guard

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_cycle(self) -> Dict[str, dict]:
        """Run one full cycle across all configured symbols.

        Returns a dict keyed by symbol, each value containing:
            {
              "signals_generated": int,
              "signals_approved": int,
              "signals_dispatched": int,
              "regime": str,
              "ob_count": int,
            }
        """
        summary: Dict[str, dict] = {}
        t0 = time.monotonic()
        logger.info("=== Cycle start ===")

        for symbol in self.config.symbols:
            summary[symbol] = self._run_symbol(symbol)

        elapsed = time.monotonic() - t0
        logger.info(f"=== Cycle complete in {elapsed:.1f}s ===")
        return summary

    def run(self) -> None:
        """Main loop — run_cycle() every config.poll_interval seconds."""
        self._log_startup()
        try:
            while True:
                self.run_cycle()
                logger.info(f"Sleeping {self.config.poll_interval}s ...")
                time.sleep(self.config.poll_interval)
        except KeyboardInterrupt:
            logger.info("Orchestrator stopped by user.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_symbol(self, symbol: str) -> dict:
        """Run one full pipeline for a single symbol. Catches all exceptions."""
        result = {
            "signals_generated": 0,
            "signals_approved": 0,
            "signals_dispatched": 0,
            "regime": "UNKNOWN",
            "ob_count": 0,
        }
        try:
            # 1. Fetch / update OHLCV
            df = update_ohlcv(
                self.config.data_dir,
                symbol,
                self.config.timeframe,
                self.config.lookback_days,
            )

            # 2. Regime classification
            regimes = classify_regime(df, adx_trend_threshold=self.config.adx_trend_threshold)
            known = regimes.dropna()
            latest_regime = str(known.iloc[-1]) if not known.empty else "UNKNOWN"
            result["regime"] = latest_regime
            logger.info(f"[{symbol}] regime={latest_regime}")

            # 3. Order blocks
            obs = detect_order_blocks(df)
            obs = mark_mitigated(obs, df)
            active_obs = [o for o in obs if o.active]
            result["ob_count"] = len(active_obs)
            logger.info(f"[{symbol}] active_obs={len(active_obs)}")

            # 4. Signal generation
            signals = generate_signals(
                df, symbol, obs, regimes,
                size=self.config.lot_size,
                sl_buffer=self.config.sl_buffer,
                rr_ratio=self.config.rr_ratio,
            )
            result["signals_generated"] = len(signals)
            logger.info(f"[{symbol}] signals_generated={len(signals)}")

            # 5. Risk gate + dispatch
            approved_count = 0
            dispatched_count = 0
            for signal in signals:
                ok, reason = self.risk_manager.check_signal(signal, self.open_positions)
                if ok:
                    approved_count += 1
                    dispatched = self._dispatch_signal(signal)
                    if dispatched:
                        dispatched_count += 1
                else:
                    logger.info(f"[{symbol}] signal rejected: {reason}")

            result["signals_approved"] = approved_count
            result["signals_dispatched"] = dispatched_count

        except Exception:
            logger.exception(f"[{symbol}] Error in run cycle — skipping symbol")

        return result

    def _dispatch_signal(self, signal: TradeSignal) -> bool:
        """Write signal to bridge. Skip if already dispatched this session.

        Returns True if the file was written, False if skipped (duplicate).
        """
        # Build a dedup key from the signal's comment (which contains OB timestamp + regime)
        # or fall back to symbol_side
        sig_id = signal.comment or f"{signal.symbol}_{signal.side}"
        if sig_id in self._dispatched_ids:
            logger.debug(f"Skipping duplicate signal: {sig_id}")
            return False

        paths = execute_signals([signal], self.config.bridge_folder)
        self._dispatched_ids.add(sig_id)
        logger.info(
            f"Dispatched: {signal.symbol} {signal.side} {signal.size}L  "
            f"SL={signal.stop_loss:.5f} TP={signal.take_profit:.5f}  "
            f"file={paths[0] if paths else 'n/a'}"
        )
        return True

    def _update_positions_from_feedback(self) -> None:
        """Stub for Phase 16: position book update from feedback files. No-op."""
        pass

    def _log_startup(self) -> None:
        """Log configuration summary on startup."""
        cfg = self.config
        logger.info("Orchestrator starting")
        logger.info(f"  symbols       : {cfg.symbols}")
        logger.info(f"  timeframe     : {cfg.timeframe}")
        logger.info(f"  bridge_folder : {cfg.bridge_folder}")
        logger.info(f"  poll_interval : {cfg.poll_interval}s")
        logger.info(f"  lot_size      : {cfg.lot_size}")
        logger.info(f"  max_trades    : {cfg.max_open_trades}")
