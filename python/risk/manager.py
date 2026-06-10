"""
python/risk/manager.py

Risk Manager — Phase 13.

Pre-execution risk gate: validates every TradeSignal against configurable
limits before it is dispatched to the bridge. Designed as a pure function
layer — check_signal() reads state, returns a verdict, and writes nothing.

Guards (checked in this order):
  1. max_open_trades       — total position count cap
  2. max_lot_per_symbol    — per-symbol exposure cap (sum of open lots)
  3. max_lot_total         — total exposure cap across all symbols
  4. max_drawdown_pct      — equity drawdown kill-switch (optional)

Usage:
    rm = RiskManager(max_open_trades=5, max_lot_per_symbol=0.5)
    approved, reason = rm.check_signal(signal, open_positions)
    if approved:
        signal_to_action(signal, bridge_folder)

Architecture note:
    check_signal() is a pure function — no file I/O, no side effects.
    It is the single choke-point that every signal must pass before execution.
    Add new guards here; keep execution logic in strategy.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from signals.strategy import TradeSignal


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """An open position as reported by the EA / broker.

    In production this is populated from EA feedback files or a broker API.
    For risk checking, symbol and size are the only required fields.

    Attributes:
        symbol:      Broker symbol, e.g. "EURUSDm".
        side:        "BUY" or "SELL".
        size:        Lot size.
        entry_price: Fill price (optional — not used in risk math).
        stop_loss:   SL price (optional).
        take_profit: TP price (optional).
        ticket:      Broker order ticket (optional — for audit/logging).
    """
    symbol:      str
    side:        str          # "BUY" or "SELL"
    size:        float        # lot size
    entry_price: float = 0.0
    stop_loss:   float = 0.0
    take_profit: float = 0.0
    ticket:      str   = ""


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------

@dataclass
class RiskManager:
    """Pre-execution risk gate.

    All limits are configurable. Defaults are conservative — suitable for
    a single retail account trading small lots on one or two symbols.

    Attributes:
        max_open_trades:    Maximum number of open positions at any time.
        max_lot_per_symbol: Maximum total lot size per symbol (both sides).
        max_lot_total:      Maximum total lot size across all symbols.
        max_drawdown_pct:   Maximum % drawdown from initial equity allowed.
                            Set to 0 to disable the drawdown check.
    """
    max_open_trades:    int   = 5
    max_lot_per_symbol: float = 1.0
    max_lot_total:      float = 5.0
    max_drawdown_pct:   float = 10.0   # 0 = disabled

    # ------------------------------------------------------------------
    # Public gate — call this before every signal dispatch
    # ------------------------------------------------------------------

    def check_signal(
        self,
        signal: TradeSignal,
        open_positions: List[Position],
        current_equity: Optional[float] = None,
        initial_equity: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Validate *signal* against all configured risk limits.

        Checks are applied in strict priority order; the first breach
        short-circuits and returns its reason without evaluating the rest.

        Args:
            signal:          TradeSignal from generate_signals().
            open_positions:  Currently open positions (from EA or broker).
            current_equity:  Current account equity. If None, drawdown
                             check is skipped.
            initial_equity:  Starting equity baseline. If None, drawdown
                             check is skipped.

        Returns:
            (True,  "OK")        — all checks passed, signal may be executed.
            (False, "<reason>")  — at least one check failed; do not execute.
        """
        # 1. Max open trades
        if len(open_positions) >= self.max_open_trades:
            return False, (
                f"max_open_trades reached: "
                f"{len(open_positions)}/{self.max_open_trades}"
            )

        # 2. Per-symbol lot cap (existing exposure + new signal size)
        sym_lots = self.lots_for_symbol(signal.symbol, open_positions)
        if sym_lots + signal.size > self.max_lot_per_symbol:
            return False, (
                f"max_lot_per_symbol exceeded for {signal.symbol}: "
                f"{sym_lots + signal.size:.4f} > {self.max_lot_per_symbol:.4f}"
            )

        # 3. Total lot cap (existing exposure + new signal size)
        total = self.total_lots(open_positions)
        if total + signal.size > self.max_lot_total:
            return False, (
                f"max_lot_total exceeded: "
                f"{total + signal.size:.4f} > {self.max_lot_total:.4f}"
            )

        # 4. Drawdown kill-switch (only when equity args provided and limit > 0)
        if (
            self.max_drawdown_pct
            and current_equity is not None
            and initial_equity is not None
            and initial_equity > 0
        ):
            dd = self.drawdown_pct(current_equity, initial_equity)
            if dd >= self.max_drawdown_pct:
                return False, (
                    f"max_drawdown_pct breached: "
                    f"{dd:.2f}% >= {self.max_drawdown_pct:.2f}%"
                )

        return True, "OK"

    # ------------------------------------------------------------------
    # Inspection helpers — pure, no side effects
    # ------------------------------------------------------------------

    def lots_for_symbol(self, symbol: str, positions: List[Position]) -> float:
        """Total lot exposure for *symbol* across all open positions.

        Both BUY and SELL are summed — this measures gross exposure, not net.
        """
        return sum(p.size for p in positions if p.symbol == symbol)

    def total_lots(self, positions: List[Position]) -> float:
        """Total lot exposure across all open positions (all symbols)."""
        return sum(p.size for p in positions)

    def drawdown_pct(self, current_equity: float, initial_equity: float) -> float:
        """Percentage drawdown from *initial_equity*.

        Returns 0.0 if initial_equity is zero or if current equity exceeds
        the initial (profit scenario — not a drawdown).

        Args:
            current_equity:  Current account equity.
            initial_equity:  Baseline equity at session/strategy start.

        Returns:
            A non-negative float: 0.0 = no drawdown, 100.0 = total loss.
        """
        if initial_equity <= 0:
            return 0.0
        return max(0.0, (initial_equity - current_equity) / initial_equity * 100.0)

    def is_drawdown_breached(
        self,
        current_equity: float,
        initial_equity: float,
    ) -> bool:
        """True if drawdown has reached or exceeded the configured limit.

        Always returns False when max_drawdown_pct is 0 (disabled).
        """
        if not self.max_drawdown_pct:
            return False
        return self.drawdown_pct(current_equity, initial_equity) >= self.max_drawdown_pct
