"""
python/signals/strategy.py

Strategy signal generation — Phase 12b.

Combines regime detection and order block detection to produce trade signals.
Signals are regime-filtered: only OBs aligned with the current trend are
traded. Signals can optionally be written to action files via action_writer.

Signal logic:
  TRENDING_UP   → look for bullish OB retests → BUY signals
  TRENDING_DOWN → look for bearish OB retests → SELL signals
  RANGING / VOLATILE / UNKNOWN → no signals generated

Entry:  price touches (or enters) the OB zone from the correct side
SL:     below ob_low (BUY) or above ob_high (SELL) + sl_buffer
TP:     entry +/- rr_ratio × (entry - SL)  →  default 2:1 R:R

Architecture note:
  generate_signals() is a pure function — it reads data, returns signals,
  writes nothing. signal_to_action() is the side-effecting layer that
  writes action files. Keep them separate for testability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from signals.order_blocks import OrderBlock, price_in_zone
from signals.regime import RegimeLabel
from bridge.action_writer import write_open_action


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """A single actionable trade signal produced by the strategy.

    Attributes:
        timestamp:   Bar timestamp when the signal was generated.
        symbol:      Broker symbol, e.g. "EURUSDm".
        side:        "BUY" or "SELL".
        entry_price: Mid-price at signal generation (indicative).
        stop_loss:   Absolute SL price level.
        take_profit: Absolute TP price level.
        size:        Lot size (passed through from caller, default 0.01).
        regime:      Market regime at time of signal.
        ob:          The OrderBlock that triggered this signal.
        comment:     Free-text description for audit/logging.
    """
    timestamp:   pd.Timestamp
    symbol:      str
    side:        str
    entry_price: float
    stop_loss:   float
    take_profit: float
    size:        float
    regime:      RegimeLabel
    ob:          OrderBlock
    comment:     str = ""

    @property
    def risk_pips(self) -> float:
        """Distance from entry to SL (always positive)."""
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_pips(self) -> float:
        """Distance from entry to TP (always positive)."""
        return abs(self.take_profit - self.entry_price)

    @property
    def rr_ratio(self) -> float:
        """Reward-to-risk ratio. Returns 0.0 if risk is zero."""
        if self.risk_pips == 0:
            return 0.0
        return self.reward_pips / self.risk_pips


# ---------------------------------------------------------------------------
# Signal generation (pure — no side effects)
# ---------------------------------------------------------------------------

def generate_signals(
    df: pd.DataFrame,
    symbol: str,
    obs: List[OrderBlock],
    regimes: pd.Series,
    size: float           = 0.01,
    sl_buffer: float      = 0.0002,
    rr_ratio: float       = 2.0,
    tolerance: float      = 0.0002,
) -> List[TradeSignal]:
    """Generate regime-filtered trade signals from order block retests.

    For each bar in *df*, checks whether:
      1. The regime is directional (TRENDING_UP or TRENDING_DOWN).
      2. An active OB aligned with the regime is being retested by price.
      3. The retest direction matches (price entering OB from correct side).

    Args:
        df:         OHLCV DataFrame (from data/ohlcv.py).
        symbol:     Broker symbol string, e.g. "EURUSDm".
        obs:        List of OrderBlock objects (from detect_order_blocks).
        regimes:    pd.Series of RegimeLabel indexed like df (from classify_regime).
        size:       Lot size for generated signals. Default 0.01.
        sl_buffer:  Extra distance beyond OB edge for SL placement. Default 0.0002.
        rr_ratio:   Take-profit multiple of risk. Default 2.0 (2:1 R:R).
        tolerance:  Price tolerance for entering the OB zone. Default 0.0002.

    Returns:
        List of TradeSignal, one per OB retest detected. An OB triggers at
        most one signal — subsequent retests of the same OB are ignored.
    """
    signals: List[TradeSignal] = []
    triggered_obs: set = set()   # track OB timestamps already signalled

    for i in range(len(df)):
        bar     = df.iloc[i]
        ts      = df.index[i]
        regime  = regimes.iloc[i] if i < len(regimes) else RegimeLabel.UNKNOWN

        if regime == RegimeLabel.UNKNOWN:
            continue

        mid_price = (bar["high"] + bar["low"]) / 2.0

        for ob in obs:
            # Skip OBs not yet formed, already triggered, or inactive
            if ob.timestamp >= ts:
                continue
            if id(ob) in triggered_obs:
                continue
            if not ob.active:
                continue

            # Regime-OB alignment check
            if regime == RegimeLabel.TRENDING_UP and ob.side != "BULLISH":
                continue
            if regime == RegimeLabel.TRENDING_DOWN and ob.side != "BEARISH":
                continue
            if regime in (RegimeLabel.RANGING, RegimeLabel.VOLATILE):
                continue

            # Retest check: price enters the OB zone
            if not price_in_zone(mid_price, ob, tolerance=tolerance):
                continue

            # Build signal
            if ob.side == "BULLISH":
                sl = ob.ob_low - sl_buffer
                tp = mid_price + rr_ratio * (mid_price - sl)
            else:
                sl = ob.ob_high + sl_buffer
                tp = mid_price - rr_ratio * (sl - mid_price)

            side = "BUY" if ob.side == "BULLISH" else "SELL"

            signal = TradeSignal(
                timestamp   = ts,
                symbol      = symbol,
                side        = side,
                entry_price = mid_price,
                stop_loss   = round(sl, 5),
                take_profit = round(tp, 5),
                size        = size,
                regime      = regime,
                ob          = ob,
                comment     = (
                    f"OB_{ob.side}_{ob.timestamp.strftime('%Y%m%d_%H%M')}"
                    f"_regime_{regime.value}"
                ),
            )
            signals.append(signal)
            triggered_obs.add(id(ob))

    return signals


# ---------------------------------------------------------------------------
# Action file writer (side-effectful layer)
# ---------------------------------------------------------------------------

def signal_to_action(
    signal: TradeSignal,
    bridge_folder: str,
    magic: int = 0,
) -> str:
    """Write a TradeSignal to a bridge action file via write_open_action.

    Args:
        signal:        TradeSignal from generate_signals().
        bridge_folder: Path to the EA's BridgeFolder (outgoing directory).
        magic:         Optional magic number override. Default 0 (EA default).

    Returns:
        Full path of the written action file.

    Raises:
        IOError: if the action file cannot be written.
    """
    path = write_open_action(
        folder      = bridge_folder,
        asset       = signal.symbol,
        side        = signal.side,
        size        = signal.size,
        sl          = signal.stop_loss,
        tp          = signal.take_profit,
        comment     = signal.comment,
        magic       = magic,
    )
    return path


def execute_signals(
    signals: List[TradeSignal],
    bridge_folder: str,
    magic: int = 0,
) -> List[str]:
    """Write all signals to bridge action files.

    Args:
        signals:       List of TradeSignal from generate_signals().
        bridge_folder: Path to the EA's BridgeFolder.
        magic:         Optional magic number override.

    Returns:
        List of file paths written, one per signal.
    """
    return [signal_to_action(s, bridge_folder, magic) for s in signals]
