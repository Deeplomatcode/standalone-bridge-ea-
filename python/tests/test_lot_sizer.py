"""Phase 19 — Unit tests for risk/lot_sizer.py.

Tests cover:
  - calculate_lot_size: normal case, zero/negative stop, risk_pct ≤ 0,
    lot clamping at min and max, rounding to 0.01 step, contract_size variation
  - size_signals: empty list, immutability of originals, correct size applied,
    lot_max from config.max_lot_per_symbol
  - TradingConfig: new field defaults and from_env() overrides
"""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pandas as pd
import pytest

from risk.lot_sizer import LOT_STEP, calculate_lot_size, size_signals
from core.config import TradingConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> TradingConfig:
    """TradingConfig with Phase 19 fields, overridable via kwargs."""
    defaults = dict(
        account_equity=10_000.0,
        risk_pct_per_trade=1.0,
        contract_size=100_000.0,
        max_lot_per_symbol=0.10,
    )
    defaults.update(kwargs)
    return TradingConfig(**defaults)


def make_signal(entry: float = 1.1050, sl: float = 1.1000) -> MagicMock:
    """Minimal mock TradeSignal with risk_pips = abs(entry - sl)."""
    sig = MagicMock(spec=["symbol", "side", "entry_price", "stop_loss",
                           "take_profit", "size", "risk_pips", "timestamp",
                           "regime", "ob", "comment"])
    sig.symbol      = "EURUSDm"
    sig.side        = "BUY"
    sig.entry_price = entry
    sig.stop_loss   = sl
    sig.risk_pips   = abs(entry - sl)
    sig.size        = 0.01           # original size — should be overwritten
    sig.timestamp   = pd.Timestamp("2026-01-05 08:30:00")
    return sig


# ---------------------------------------------------------------------------
# calculate_lot_size — normal cases
# ---------------------------------------------------------------------------

class TestCalculateLotSizeNormal:

    def test_standard_eurusd_50_pip_stop(self):
        # equity=10000, risk=1%, stop=0.005, contract=100000
        # risk_amount=100, risk_per_lot=500 → 0.20 lots
        result = calculate_lot_size(
            equity=10_000, risk_pct=1.0, stop_distance=0.005,
            contract_size=100_000, lot_min=0.01, lot_max=10.0,
        )
        assert result == 0.20

    def test_small_equity_tight_stop(self):
        # equity=1000, risk=2%, stop=0.0010, contract=100000
        # risk_amount=20, risk_per_lot=100 → 0.20 lots
        result = calculate_lot_size(
            equity=1_000, risk_pct=2.0, stop_distance=0.001,
            contract_size=100_000, lot_min=0.01, lot_max=10.0,
        )
        assert result == 0.20

    def test_gold_contract_size(self):
        # equity=10000, risk=1%, stop=5.0, contract=100
        # risk_amount=100, risk_per_lot=500 → 0.20 lots
        result = calculate_lot_size(
            equity=10_000, risk_pct=1.0, stop_distance=5.0,
            contract_size=100, lot_min=0.01, lot_max=10.0,
        )
        assert result == 0.20

    def test_result_is_float(self):
        result = calculate_lot_size(10_000, 1.0, 0.005, 100_000, 0.01, 10.0)
        assert isinstance(result, float)

    def test_result_rounded_to_two_decimals(self):
        result = calculate_lot_size(10_000, 1.0, 0.0033, 100_000, 0.01, 10.0)
        assert result == round(result, 2)

    def test_lot_step_constant(self):
        assert LOT_STEP == 0.01


# ---------------------------------------------------------------------------
# calculate_lot_size — safety guards (zero / negative stop)
# ---------------------------------------------------------------------------

class TestCalculateLotSizeZeroStop:

    def test_zero_stop_returns_lot_min(self):
        result = calculate_lot_size(10_000, 1.0, 0.0, 100_000, 0.01, 10.0)
        assert result == 0.01

    def test_negative_stop_returns_lot_min(self):
        result = calculate_lot_size(10_000, 1.0, -0.005, 100_000, 0.01, 10.0)
        assert result == 0.01

    def test_zero_risk_pct_returns_lot_min(self):
        result = calculate_lot_size(10_000, 0.0, 0.005, 100_000, 0.01, 10.0)
        assert result == 0.01

    def test_negative_risk_pct_returns_lot_min(self):
        result = calculate_lot_size(10_000, -1.0, 0.005, 100_000, 0.01, 10.0)
        assert result == 0.01


# ---------------------------------------------------------------------------
# calculate_lot_size — clamping
# ---------------------------------------------------------------------------

class TestCalculateLotSizeClamping:

    def test_below_lot_min_clamped_up(self):
        # Very large stop → tiny raw lot → should be clamped to lot_min
        result = calculate_lot_size(
            equity=100, risk_pct=1.0, stop_distance=1.0,
            contract_size=100_000, lot_min=0.01, lot_max=10.0,
        )
        assert result == 0.01

    def test_above_lot_max_clamped_down(self):
        # Huge equity, tiny stop → enormous raw lot → clamped to lot_max
        result = calculate_lot_size(
            equity=10_000_000, risk_pct=10.0, stop_distance=0.0001,
            contract_size=100_000, lot_min=0.01, lot_max=0.10,
        )
        assert result == 0.10

    def test_custom_lot_max(self):
        result = calculate_lot_size(
            equity=10_000, risk_pct=1.0, stop_distance=0.0001,
            contract_size=100_000, lot_min=0.01, lot_max=0.05,
        )
        assert result <= 0.05

    def test_result_never_below_lot_min(self):
        for stop in [0.001, 0.01, 0.10, 1.0]:
            result = calculate_lot_size(500, 0.5, stop, 100_000, 0.01, 10.0)
            assert result >= 0.01


# ---------------------------------------------------------------------------
# size_signals
# ---------------------------------------------------------------------------

class TestSizeSignals:

    def test_empty_list_returns_empty(self):
        cfg = make_config()
        assert size_signals([], cfg) == []

    def test_returns_same_list(self):
        cfg = make_config()
        sig = make_signal()
        original_list = [sig]
        result = size_signals(original_list, cfg)
        assert result is original_list   # mutates in place, returns same list

    def test_signal_size_updated_in_place(self):
        cfg = make_config(
            account_equity=10_000, risk_pct_per_trade=1.0,
            contract_size=100_000, max_lot_per_symbol=10.0,
        )
        sig = make_signal(entry=1.1050, sl=1.1000)  # stop=0.005 → 0.20 lots
        size_signals([sig], cfg)
        assert sig.size == 0.20          # mutated on the original object

    def test_sized_signal_has_correct_lot(self):
        # stop=0.005, equity=10000, risk=1%, contract=100000 → 0.20 lots
        cfg = make_config(
            account_equity=10_000, risk_pct_per_trade=1.0,
            contract_size=100_000, max_lot_per_symbol=10.0,
        )
        sig = make_signal(entry=1.1050, sl=1.1000)  # stop=0.005
        result = size_signals([sig], cfg)
        assert result[0].size == 0.20

    def test_lot_max_respects_config_max_lot_per_symbol(self):
        # Very tight stop would give huge lot, but capped by max_lot_per_symbol
        cfg = make_config(
            account_equity=1_000_000, risk_pct_per_trade=10.0,
            contract_size=100_000, max_lot_per_symbol=0.05,
        )
        sig = make_signal(entry=1.1050, sl=1.1049)  # 0.0001 stop → enormous raw lot
        result = size_signals([sig], cfg)
        assert result[0].size <= 0.05

    def test_multiple_signals_each_sized(self):
        cfg = make_config(
            account_equity=10_000, risk_pct_per_trade=1.0,
            contract_size=100_000, max_lot_per_symbol=10.0,
        )
        sig1 = make_signal(entry=1.1050, sl=1.1000)  # stop=0.005 → 0.20 lots
        sig2 = make_signal(entry=1.1050, sl=1.1025)  # stop=0.0025 → 0.40 lots
        result = size_signals([sig1, sig2], cfg)
        assert len(result) == 2
        assert result[0].size == 0.20
        assert result[1].size == 0.40

    def test_real_tradesignal_size_updated(self):
        """size_signals works correctly with real TradeSignal dataclass objects."""
        from signals.strategy import TradeSignal
        from signals.regime import RegimeLabel
        from signals.order_blocks import OrderBlock

        ob = OrderBlock(
            timestamp=pd.Timestamp("2026-01-01"),
            side="BULLISH",
            ob_high=1.11,
            ob_low=1.10,
            impulse_pct=0.003,
        )
        signal = TradeSignal(
            timestamp=pd.Timestamp("2026-01-05 08:30:00"),
            symbol="EURUSDm",
            side="BUY",
            entry_price=1.1050,
            stop_loss=1.1000,  # stop = 0.005
            take_profit=1.1150,
            size=0.01,
            regime=RegimeLabel.TRENDING_UP,
            ob=ob,
        )
        cfg = make_config(
            account_equity=10_000, risk_pct_per_trade=1.0,
            contract_size=100_000, max_lot_per_symbol=10.0,
        )
        size_signals([signal], cfg)
        # In-place update — stop=0.005, equity=10000, risk=1% → 0.20 lots
        assert signal.size == 0.20


# ---------------------------------------------------------------------------
# TradingConfig — Phase 19 fields
# ---------------------------------------------------------------------------

class TestTradingConfigPhase19:

    def test_risk_pct_per_trade_default(self):
        cfg = TradingConfig()
        assert cfg.risk_pct_per_trade == 1.0

    def test_account_equity_default(self):
        cfg = TradingConfig()
        assert cfg.account_equity == 10_000.0

    def test_contract_size_default(self):
        cfg = TradingConfig()
        assert cfg.contract_size == 100_000.0

    def test_from_env_risk_pct(self, monkeypatch):
        monkeypatch.setenv("RISK_PCT_PER_TRADE", "2.0")
        cfg = TradingConfig.from_env()
        assert cfg.risk_pct_per_trade == 2.0

    def test_from_env_account_equity(self, monkeypatch):
        monkeypatch.setenv("ACCOUNT_EQUITY", "50000")
        cfg = TradingConfig.from_env()
        assert cfg.account_equity == 50_000.0

    def test_from_env_contract_size(self, monkeypatch):
        monkeypatch.setenv("CONTRACT_SIZE", "100")
        cfg = TradingConfig.from_env()
        assert cfg.contract_size == 100.0

    def test_from_env_defaults_when_absent(self, monkeypatch):
        monkeypatch.delenv("RISK_PCT_PER_TRADE", raising=False)
        monkeypatch.delenv("ACCOUNT_EQUITY", raising=False)
        monkeypatch.delenv("CONTRACT_SIZE", raising=False)
        cfg = TradingConfig.from_env()
        assert cfg.risk_pct_per_trade == 1.0
        assert cfg.account_equity == 10_000.0
        assert cfg.contract_size == 100_000.0
