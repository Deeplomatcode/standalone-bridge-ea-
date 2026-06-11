"""Phase 19 — Risk-based lot sizing.

Replaces fixed lot_size=0.01 with dynamic sizing driven by account equity
and the signal's actual stop-loss distance.

Formula:
    risk_amount  = equity × risk_pct / 100
    risk_per_lot = stop_distance × contract_size   (quote-currency units)
    lot_size     = risk_amount / risk_per_lot

This ensures every trade risks exactly risk_pct of equity regardless of
stop width — wider stops yield smaller positions, tight stops yield larger
ones (up to lot_max).

Contract sizes (set in TradingConfig.contract_size):
    Forex (EURUSD, GBPUSD, etc.):  100 000  (default)
    Gold  (XAUUSD):                    100
    CFDs vary — configure per instrument family.

Example:
    equity=10 000, risk_pct=1 %, stop=0.0050, contract_size=100 000
    → risk_amount = 100 USD
    → risk_per_lot = 0.005 × 100 000 = 500 USD/lot
    → lot_size = 100 / 500 = 0.20 lots
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

#: Minimum lot increment used when rounding (standard broker step).
LOT_STEP = 0.01


def calculate_lot_size(
    equity:        float,
    risk_pct:      float,
    stop_distance: float,
    contract_size: float = 100_000.0,
    lot_min:       float = 0.01,
    lot_max:       float = 10.0,
) -> float:
    """Compute a risk-adjusted lot size.

    Args:
        equity:        Account balance / equity in USD.
        risk_pct:      Risk per trade as a percentage (e.g. 1.0 = 1 %).
        stop_distance: Absolute price distance from entry to SL (always > 0).
        contract_size: Units per lot. Default 100 000 (standard FX lot).
        lot_min:       Minimum returnable lot size. Default 0.01.
        lot_max:       Maximum returnable lot size. Default 10.0.

    Returns:
        Lot size rounded to 2 decimal places, clamped to [lot_min, lot_max].
        If ``stop_distance`` is zero or negative, returns ``lot_min``
        (safety guard — should not happen with valid signals).
    """
    if stop_distance <= 0.0:
        logger.warning(
            "calculate_lot_size: stop_distance=%.6f ≤ 0 — returning lot_min=%.2f",
            stop_distance,
            lot_min,
        )
        return lot_min

    if risk_pct <= 0.0:
        logger.warning(
            "calculate_lot_size: risk_pct=%.4f ≤ 0 — returning lot_min=%.2f",
            risk_pct,
            lot_min,
        )
        return lot_min

    risk_amount  = equity * risk_pct / 100.0
    risk_per_lot = stop_distance * contract_size

    raw_lots = risk_amount / risk_per_lot

    # Round to nearest LOT_STEP, then clamp
    stepped = round(raw_lots / LOT_STEP) * LOT_STEP
    clamped = max(lot_min, min(lot_max, stepped))
    return round(clamped, 2)


def size_signals(signals: list, config) -> list:
    """Overwrite the size field of each signal with risk-based lot sizing.

    Uses:
        config.account_equity      — account balance in USD
        config.risk_pct_per_trade  — % of equity to risk per trade
        config.contract_size       — units per lot
        config.max_lot_per_symbol  — upper lot bound (lot_max)

    Args:
        signals: List of ``TradeSignal`` from ``generate_signals()`` /
                 ``filter_by_session()``.
        config:  ``TradingConfig`` supplying equity and risk parameters.

    Returns:
        The same *signals* list with ``size`` updated in place on each signal.
        ``TradeSignal`` is not a frozen dataclass, so direct mutation is safe.
        Returns an empty list if *signals* is empty.
    """
    for signal in signals:
        stop_distance = signal.risk_pips  # abs(entry_price - stop_loss)
        lot_size = calculate_lot_size(
            equity=config.account_equity,
            risk_pct=config.risk_pct_per_trade,
            stop_distance=stop_distance,
            contract_size=config.contract_size,
            lot_min=0.01,
            lot_max=config.max_lot_per_symbol,
        )
        signal.size = lot_size
        logger.debug(
            "size_signals: %s %s  stop=%.5f  equity=%.0f  risk=%.1f%%"
            "  contract=%.0f  → lot=%.2f",
            signal.symbol,
            signal.side,
            stop_distance,
            config.account_equity,
            config.risk_pct_per_trade,
            config.contract_size,
            lot_size,
        )
    return signals
