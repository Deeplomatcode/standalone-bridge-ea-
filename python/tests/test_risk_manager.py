"""
python/tests/test_risk_manager.py

Unit tests for risk/manager.py — Phase 13.

Tests cover:
  - Position dataclass field defaults
  - RiskManager default values
  - lots_for_symbol: empty, single, multi, cross-symbol, both sides
  - total_lots: empty, single, multi-symbol
  - drawdown_pct: zero, partial, full, profit, zero-baseline
  - is_drawdown_breached: true, false, at-limit, disabled (0)
  - check_signal: approved path + every rejection path
"""

import pytest
import pandas as pd

from signals.order_blocks import OrderBlock
from signals.regime import RegimeLabel
from signals.strategy import TradeSignal
from risk.manager import Position, RiskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(symbol: str = "EURUSDm", side: str = "BUY", size: float = 0.01):
    ob = OrderBlock(
        timestamp=pd.Timestamp("2026-01-01", tz="UTC"),
        side="BULLISH", ob_high=1.105, ob_low=1.100, impulse_pct=0.005,
    )
    return TradeSignal(
        timestamp=pd.Timestamp("2026-01-01 10:00", tz="UTC"),
        symbol=symbol, side=side,
        entry_price=1.102, stop_loss=1.099, take_profit=1.108,
        size=size, regime=RegimeLabel.TRENDING_UP, ob=ob,
    )


def _make_position(symbol: str = "EURUSDm", size: float = 0.01, side: str = "BUY"):
    return Position(symbol=symbol, side=side, size=size, entry_price=1.100)


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------

class TestPosition:

    def test_required_fields(self):
        p = Position(symbol="EURUSDm", side="BUY", size=0.01)
        assert p.symbol == "EURUSDm"
        assert p.side   == "BUY"
        assert p.size   == 0.01

    def test_optional_fields_default_to_zero_or_empty(self):
        p = Position(symbol="EURUSDm", side="BUY", size=0.01)
        assert p.entry_price == 0.0
        assert p.stop_loss   == 0.0
        assert p.take_profit == 0.0
        assert p.ticket      == ""

    def test_optional_fields_settable(self):
        p = Position(symbol="XAUUSDm", side="SELL", size=0.10,
                     entry_price=2300.0, ticket="999")
        assert p.entry_price == 2300.0
        assert p.ticket      == "999"


# ---------------------------------------------------------------------------
# RiskManager defaults
# ---------------------------------------------------------------------------

class TestRiskManagerDefaults:

    def test_default_values(self):
        rm = RiskManager()
        assert rm.max_open_trades    == 5
        assert rm.max_lot_per_symbol == 1.0
        assert rm.max_lot_total      == 5.0
        assert rm.max_drawdown_pct   == 10.0

    def test_custom_values(self):
        rm = RiskManager(max_open_trades=3, max_lot_per_symbol=0.5,
                         max_lot_total=2.0, max_drawdown_pct=5.0)
        assert rm.max_open_trades    == 3
        assert rm.max_lot_per_symbol == 0.5


# ---------------------------------------------------------------------------
# lots_for_symbol
# ---------------------------------------------------------------------------

class TestLotsForSymbol:

    def test_no_positions_returns_zero(self):
        assert RiskManager().lots_for_symbol("EURUSDm", []) == 0.0

    def test_single_matching_position(self):
        pos = [_make_position("EURUSDm", 0.05)]
        assert RiskManager().lots_for_symbol("EURUSDm", pos) == pytest.approx(0.05)

    def test_multiple_positions_same_symbol_summed(self):
        pos = [_make_position("EURUSDm", 0.05), _make_position("EURUSDm", 0.03)]
        assert RiskManager().lots_for_symbol("EURUSDm", pos) == pytest.approx(0.08)

    def test_ignores_other_symbols(self):
        pos = [_make_position("EURUSDm", 0.05), _make_position("XAUUSDm", 0.10)]
        assert RiskManager().lots_for_symbol("EURUSDm", pos) == pytest.approx(0.05)

    def test_buy_and_sell_both_counted_gross(self):
        """Gross exposure: both sides add to the symbol total."""
        pos = [_make_position("EURUSDm", 0.05, "BUY"),
               _make_position("EURUSDm", 0.03, "SELL")]
        assert RiskManager().lots_for_symbol("EURUSDm", pos) == pytest.approx(0.08)

    def test_symbol_not_in_positions_returns_zero(self):
        pos = [_make_position("XAUUSDm", 0.10)]
        assert RiskManager().lots_for_symbol("EURUSDm", pos) == 0.0


# ---------------------------------------------------------------------------
# total_lots
# ---------------------------------------------------------------------------

class TestTotalLots:

    def test_empty_returns_zero(self):
        assert RiskManager().total_lots([]) == 0.0

    def test_single_position(self):
        assert RiskManager().total_lots([_make_position(size=0.10)]) == pytest.approx(0.10)

    def test_multiple_symbols_summed(self):
        pos = [_make_position("EURUSDm", 0.05), _make_position("XAUUSDm", 0.10)]
        assert RiskManager().total_lots(pos) == pytest.approx(0.15)

    def test_multiple_positions_same_symbol_summed(self):
        pos = [_make_position("EURUSDm", 0.03), _make_position("EURUSDm", 0.04)]
        assert RiskManager().total_lots(pos) == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# drawdown_pct
# ---------------------------------------------------------------------------

class TestDrawdownPct:

    def test_no_drawdown(self):
        assert RiskManager().drawdown_pct(10000, 10000) == pytest.approx(0.0)

    def test_ten_percent_drawdown(self):
        assert RiskManager().drawdown_pct(9000, 10000) == pytest.approx(10.0)

    def test_fifty_percent_drawdown(self):
        assert RiskManager().drawdown_pct(5000, 10000) == pytest.approx(50.0)

    def test_full_loss(self):
        assert RiskManager().drawdown_pct(0, 10000) == pytest.approx(100.0)

    def test_profit_returns_zero_not_negative(self):
        """Equity above baseline is not a drawdown — return 0."""
        assert RiskManager().drawdown_pct(11000, 10000) == pytest.approx(0.0)

    def test_zero_initial_equity_returns_zero(self):
        assert RiskManager().drawdown_pct(9000, 0) == 0.0


# ---------------------------------------------------------------------------
# is_drawdown_breached
# ---------------------------------------------------------------------------

class TestIsDrawdownBreached:

    def test_breached_above_limit(self):
        rm = RiskManager(max_drawdown_pct=10.0)
        assert rm.is_drawdown_breached(8000, 10000) is True   # 20% > 10%

    def test_not_breached_below_limit(self):
        rm = RiskManager(max_drawdown_pct=10.0)
        assert rm.is_drawdown_breached(9500, 10000) is False  # 5% < 10%

    def test_breached_exactly_at_limit(self):
        rm = RiskManager(max_drawdown_pct=10.0)
        assert rm.is_drawdown_breached(9000, 10000) is True   # 10% == 10%

    def test_disabled_when_zero(self):
        """max_drawdown_pct=0 disables the check entirely."""
        rm = RiskManager(max_drawdown_pct=0)
        assert rm.is_drawdown_breached(0, 10000) is False

    def test_no_drawdown_not_breached(self):
        rm = RiskManager(max_drawdown_pct=10.0)
        assert rm.is_drawdown_breached(10000, 10000) is False


# ---------------------------------------------------------------------------
# check_signal
# ---------------------------------------------------------------------------

class TestCheckSignal:

    def _rm(self, **kwargs):
        defaults = dict(
            max_open_trades=5,
            max_lot_per_symbol=0.10,
            max_lot_total=0.50,
            max_drawdown_pct=10.0,
        )
        defaults.update(kwargs)
        return RiskManager(**defaults)

    # --- Approved path ---

    def test_approved_with_no_open_positions(self):
        ok, reason = self._rm().check_signal(_make_signal(size=0.01), [])
        assert ok is True
        assert reason == "OK"

    def test_approved_with_positions_under_all_limits(self):
        pos = [_make_position("XAUUSDm", 0.05)]  # different symbol, few trades
        ok, _ = self._rm().check_signal(_make_signal(size=0.01), pos)
        assert ok is True

    def test_approved_with_equity_just_under_drawdown_limit(self):
        ok, _ = self._rm().check_signal(
            _make_signal(size=0.01), [],
            current_equity=9001, initial_equity=10000,  # 9.99% < 10%
        )
        assert ok is True

    # --- Rejected: max_open_trades ---

    def test_rejected_when_open_trades_at_limit(self):
        pos    = [_make_position() for _ in range(5)]
        ok, reason = self._rm().check_signal(_make_signal(size=0.01), pos)
        assert ok is False
        assert "max_open_trades" in reason

    def test_approved_one_below_max_trades(self):
        pos = [_make_position() for _ in range(4)]
        ok, _ = self._rm().check_signal(_make_signal(size=0.01), pos)
        assert ok is True

    def test_rejection_reason_contains_counts(self):
        pos = [_make_position() for _ in range(5)]
        _, reason = self._rm(max_open_trades=5).check_signal(_make_signal(size=0.01), pos)
        assert "5" in reason

    # --- Rejected: max_lot_per_symbol ---

    def test_rejected_when_symbol_lots_would_exceed_cap(self):
        pos = [_make_position("EURUSDm", 0.08)]  # 0.08 + 0.05 = 0.13 > 0.10
        ok, reason = self._rm().check_signal(_make_signal(size=0.05), pos)
        assert ok is False
        assert "max_lot_per_symbol" in reason

    def test_rejected_when_signal_alone_exceeds_per_symbol_cap(self):
        """Signal size > cap even with no open positions."""
        ok, reason = self._rm(max_lot_per_symbol=0.05).check_signal(
            _make_signal(size=0.10), []
        )
        assert ok is False
        assert "max_lot_per_symbol" in reason

    def test_rejection_reason_contains_symbol(self):
        ok, reason = self._rm(max_lot_per_symbol=0.05).check_signal(
            _make_signal("EURUSDm", size=0.10), []
        )
        assert "EURUSDm" in reason

    def test_different_symbol_does_not_count_toward_cap(self):
        pos = [_make_position("XAUUSDm", 0.09)]  # saturates XAU, not EUR
        ok, _ = self._rm().check_signal(_make_signal("EURUSDm", size=0.05), pos)
        assert ok is True

    # --- Rejected: max_lot_total ---

    def test_rejected_when_total_lots_would_exceed_cap(self):
        pos = [_make_position("XAUUSDm", 0.48)]  # 0.48 + 0.05 = 0.53 > 0.50
        ok, reason = self._rm().check_signal(_make_signal(size=0.05), pos)
        assert ok is False
        assert "max_lot_total" in reason

    def test_approved_when_total_lots_just_under_cap(self):
        pos = [_make_position("XAUUSDm", 0.44)]  # 0.44 + 0.05 = 0.49 < 0.50
        ok, _ = self._rm().check_signal(_make_signal(size=0.05), pos)
        assert ok is True

    # --- Rejected: max_drawdown_pct ---

    def test_rejected_when_drawdown_at_limit(self):
        ok, reason = self._rm().check_signal(
            _make_signal(size=0.01), [],
            current_equity=9000, initial_equity=10000,  # exactly 10%
        )
        assert ok is False
        assert "max_drawdown_pct" in reason

    def test_rejected_when_drawdown_above_limit(self):
        ok, _ = self._rm().check_signal(
            _make_signal(size=0.01), [],
            current_equity=8000, initial_equity=10000,  # 20%
        )
        assert ok is False

    def test_drawdown_check_skipped_when_no_equity_args(self):
        """Without equity args the drawdown guard must not block the signal."""
        ok, _ = self._rm().check_signal(_make_signal(size=0.01), [])
        assert ok is True

    def test_drawdown_check_disabled_when_zero(self):
        ok, _ = self._rm(max_drawdown_pct=0).check_signal(
            _make_signal(size=0.01), [],
            current_equity=0, initial_equity=10000,
        )
        assert ok is True

    # --- Priority: first failing check wins ---

    def test_max_open_trades_checked_before_lot_limits(self):
        """With both trade count and lot limits breached, trade count reason returned."""
        pos = [_make_position() for _ in range(5)]
        _, reason = self._rm(max_open_trades=5, max_lot_per_symbol=0.001).check_signal(
            _make_signal(size=0.10), pos
        )
        assert "max_open_trades" in reason
