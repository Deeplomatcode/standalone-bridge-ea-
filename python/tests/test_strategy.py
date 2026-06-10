"""
python/tests/test_strategy.py

Unit tests for signals/strategy.py.
"""

import os
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from signals.order_blocks import OrderBlock
from signals.regime import RegimeLabel
from signals.strategy import (
    TradeSignal,
    generate_signals,
    signal_to_action,
    execute_signals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n=5, start="2026-01-01 10:00", price=1.102):
    idx = pd.date_range(start, periods=n, freq="1h", tz="UTC", name="datetime")
    return pd.DataFrame({
        "open":   [price] * n,
        "high":   [price + 0.001] * n,
        "low":    [price - 0.001] * n,
        "close":  [price] * n,
        "volume": [1000.0] * n,
    }, index=idx)


def _make_ob(side="BULLISH", high=1.104, low=1.100,
             ts="2026-01-01 09:00"):
    return OrderBlock(
        timestamp=pd.Timestamp(ts, tz="UTC"),
        side=side,
        ob_high=high,
        ob_low=low,
        impulse_pct=0.005,
        active=True,
    )


def _make_regimes(df, label: RegimeLabel):
    return pd.Series(label, index=df.index, dtype=object)


# ---------------------------------------------------------------------------
# TradeSignal dataclass
# ---------------------------------------------------------------------------

class TestTradeSignal:

    def _signal(self, entry=1.102, sl=1.100, tp=1.106):
        ob = _make_ob()
        return TradeSignal(
            timestamp=pd.Timestamp("2026-01-01 10:00", tz="UTC"),
            symbol="EURUSDm", side="BUY",
            entry_price=entry, stop_loss=sl, take_profit=tp,
            size=0.01, regime=RegimeLabel.TRENDING_UP, ob=ob,
        )

    def test_risk_pips(self):
        s = self._signal(entry=1.102, sl=1.100)
        assert s.risk_pips == pytest.approx(0.002)

    def test_reward_pips(self):
        s = self._signal(entry=1.102, tp=1.106)
        assert s.reward_pips == pytest.approx(0.004)

    def test_rr_ratio(self):
        s = self._signal(entry=1.102, sl=1.100, tp=1.106)
        assert s.rr_ratio == pytest.approx(2.0, rel=1e-3)

    def test_rr_ratio_zero_risk(self):
        s = self._signal(entry=1.102, sl=1.102, tp=1.106)
        assert s.rr_ratio == 0.0


# ---------------------------------------------------------------------------
# generate_signals
# ---------------------------------------------------------------------------

class TestGenerateSignals:

    def test_buy_signal_on_bullish_ob_retest_in_uptrend(self):
        """Price inside bullish OB zone + TRENDING_UP → BUY signal."""
        df      = _make_df(price=1.102)   # mid = 1.102, inside OB [1.100, 1.104]
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)

        signals = generate_signals(df, "EURUSDm", [ob], regimes)
        assert len(signals) == 1
        assert signals[0].side == "BUY"

    def test_sell_signal_on_bearish_ob_retest_in_downtrend(self):
        """Price inside bearish OB zone + TRENDING_DOWN → SELL signal."""
        df      = _make_df(price=1.102)   # mid inside OB [1.100, 1.104]
        ob      = _make_ob("BEARISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_DOWN)

        signals = generate_signals(df, "EURUSDm", [ob], regimes)
        assert len(signals) == 1
        assert signals[0].side == "SELL"

    def test_no_signal_in_ranging_regime(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.RANGING)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_no_signal_in_volatile_regime(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.VOLATILE)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_no_signal_in_unknown_regime(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.UNKNOWN)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_bearish_ob_ignored_in_uptrend(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BEARISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_bullish_ob_ignored_in_downtrend(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_DOWN)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_price_outside_ob_no_signal(self):
        """Price above OB zone — no retest, no signal."""
        df      = _make_df(price=1.110)   # mid well above OB [1.100, 1.104]
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_inactive_ob_no_signal(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        ob.active = False
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_ob_formed_after_bar_skipped(self):
        """OB timestamp >= bar timestamp → OB not yet formed, skip."""
        df  = _make_df(n=1, start="2026-01-01 08:00", price=1.102)
        ob  = _make_ob("BULLISH", ts="2026-01-01 09:00")   # OB in the future
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        assert generate_signals(df, "EURUSDm", [ob], regimes) == []

    def test_ob_triggers_at_most_once(self):
        """Same OB should not produce multiple signals across bars."""
        df      = _make_df(n=10, price=1.102)   # 10 bars all in OB zone
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        signals = generate_signals(df, "EURUSDm", [ob], regimes)
        assert len(signals) == 1

    def test_sl_below_ob_low_for_buy(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        s       = generate_signals(df, "EURUSDm", [ob], regimes, sl_buffer=0.0002)[0]
        assert s.stop_loss < ob.ob_low

    def test_sl_above_ob_high_for_sell(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BEARISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_DOWN)
        s       = generate_signals(df, "EURUSDm", [ob], regimes, sl_buffer=0.0002)[0]
        assert s.stop_loss > ob.ob_high

    def test_rr_ratio_honoured(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        s       = generate_signals(df, "EURUSDm", [ob], regimes, rr_ratio=3.0)[0]
        assert s.rr_ratio == pytest.approx(3.0, rel=0.01)

    def test_signal_symbol_matches(self):
        df      = _make_df(price=1.102)
        ob      = _make_ob("BULLISH", high=1.104, low=1.100)
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        s       = generate_signals(df, "XAUUSDm", [ob], regimes)[0]
        assert s.symbol == "XAUUSDm"

    def test_no_obs_no_signals(self):
        df      = _make_df()
        regimes = _make_regimes(df, RegimeLabel.TRENDING_UP)
        assert generate_signals(df, "EURUSDm", [], regimes) == []


# ---------------------------------------------------------------------------
# signal_to_action / execute_signals
# ---------------------------------------------------------------------------

class TestSignalToAction:

    def _make_signal(self, side="BUY"):
        ob = _make_ob()
        return TradeSignal(
            timestamp=pd.Timestamp("2026-01-01 10:00", tz="UTC"),
            symbol="EURUSDm", side=side,
            entry_price=1.102, stop_loss=1.099, take_profit=1.108,
            size=0.01, regime=RegimeLabel.TRENDING_UP, ob=ob,
            comment="test_signal",
        )

    def test_signal_to_action_writes_file(self, tmp_path):
        signal = self._make_signal()
        path   = signal_to_action(signal, str(tmp_path))
        assert os.path.isfile(path)

    def test_action_file_contains_correct_side(self, tmp_path):
        signal = self._make_signal(side="BUY")
        path   = signal_to_action(signal, str(tmp_path))
        content = open(path).read()
        assert "side=BUY" in content

    def test_action_file_contains_symbol(self, tmp_path):
        signal = self._make_signal()
        path   = signal_to_action(signal, str(tmp_path))
        content = open(path).read()
        assert "asset=EURUSDm" in content

    def test_execute_signals_writes_one_file_per_signal(self, tmp_path):
        signals = [self._make_signal("BUY"), self._make_signal("SELL")]
        paths   = execute_signals(signals, str(tmp_path))
        assert len(paths) == 2
        for p in paths:
            assert os.path.isfile(p)
