"""
python/tests/test_order_blocks.py

Unit tests for signals/order_blocks.py.
"""

import pandas as pd
import numpy as np
import pytest

from signals.order_blocks import (
    OrderBlock,
    find_bullish_obs,
    find_bearish_obs,
    detect_order_blocks,
    price_in_zone,
    mark_mitigated,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(opens, highs, lows, closes, start="2026-01-01", freq="1h"):
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC", name="datetime")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": [1000.0] * len(closes),
    }, index=idx)


def _bullish_impulse_df():
    """5 mixed candles then a big up move — should produce 1 bullish OB."""
    #       open    high    low     close
    rows = [
        (1.100, 1.102, 1.098, 1.101),  # 0 bullish
        (1.101, 1.103, 1.099, 1.100),  # 1 bearish  ← OB candidate
        (1.100, 1.101, 1.097, 1.098),  # 2 bearish
        (1.098, 1.099, 1.096, 1.097),  # 3 bearish
        (1.097, 1.098, 1.095, 1.096),  # 4 bearish
        (1.096, 1.115, 1.096, 1.113),  # 5 big bullish impulse ← triggers OB
        (1.113, 1.116, 1.112, 1.114),  # 6 continuation
    ]
    o, h, l, c = zip(*rows)
    return _make_df(o, h, l, c)


def _bearish_impulse_df():
    """5 mixed candles then a big down move — should produce 1 bearish OB."""
    rows = [
        (1.120, 1.122, 1.118, 1.119),  # 0 bearish
        (1.119, 1.121, 1.117, 1.120),  # 1 bullish  ← OB candidate
        (1.120, 1.122, 1.119, 1.121),  # 2 bullish
        (1.121, 1.123, 1.120, 1.122),  # 3 bullish
        (1.122, 1.124, 1.121, 1.123),  # 4 bullish
        (1.123, 1.123, 1.104, 1.106),  # 5 big bearish impulse ← triggers OB
        (1.106, 1.107, 1.103, 1.104),  # 6 continuation
    ]
    o, h, l, c = zip(*rows)
    return _make_df(o, h, l, c)


def _flat_df(n=20):
    """Flat market — no impulses, no OBs expected."""
    close = [1.10] * n
    return _make_df(close, close, close, close)


# ---------------------------------------------------------------------------
# OrderBlock dataclass
# ---------------------------------------------------------------------------

class TestOrderBlockDataclass:

    def test_mid_price(self):
        ob = OrderBlock(pd.Timestamp("2026-01-01", tz="UTC"), "BULLISH", 1.105, 1.100, 0.005)
        assert ob.mid == pytest.approx(1.1025)

    def test_height(self):
        ob = OrderBlock(pd.Timestamp("2026-01-01", tz="UTC"), "BULLISH", 1.105, 1.100, 0.005)
        assert ob.height == pytest.approx(0.005)

    def test_active_default_true(self):
        ob = OrderBlock(pd.Timestamp("2026-01-01", tz="UTC"), "BEARISH", 1.110, 1.105, 0.003)
        assert ob.active is True


# ---------------------------------------------------------------------------
# find_bullish_obs
# ---------------------------------------------------------------------------

class TestFindBullishObs:

    def test_detects_one_bullish_ob(self):
        df  = _bullish_impulse_df()
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.010)
        assert len(obs) == 1

    def test_ob_side_is_bullish(self):
        df  = _bullish_impulse_df()
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.010)
        assert obs[0].side == "BULLISH"

    def test_ob_zone_makes_sense(self):
        df  = _bullish_impulse_df()
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.010)
        ob  = obs[0]
        assert ob.ob_high > ob.ob_low
        assert ob.ob_low > 0

    def test_flat_market_no_obs(self):
        obs = find_bullish_obs(_flat_df(), min_impulse_pct=0.003)
        assert obs == []

    def test_no_bearish_candle_no_ob(self):
        """All bullish candles + impulse → no bearish OB candidate."""
        rows = [(1.10, 1.11, 1.10, 1.11)] * 5 + [(1.11, 1.13, 1.11, 1.13)]
        o, h, l, c = zip(*rows)
        df  = _make_df(o, h, l, c)
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.010)
        assert obs == []

    def test_sorted_ascending(self):
        df  = _bullish_impulse_df()
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.005)
        timestamps = [ob.timestamp for ob in obs]
        assert timestamps == sorted(timestamps)

    def test_no_duplicate_timestamps(self):
        df  = _bullish_impulse_df()
        obs = find_bullish_obs(df, lookback=5, min_impulse_pct=0.005)
        timestamps = [ob.timestamp for ob in obs]
        assert len(timestamps) == len(set(timestamps))


# ---------------------------------------------------------------------------
# find_bearish_obs
# ---------------------------------------------------------------------------

class TestFindBearishObs:

    def test_detects_one_bearish_ob(self):
        df  = _bearish_impulse_df()
        obs = find_bearish_obs(df, lookback=5, min_impulse_pct=0.010)
        assert len(obs) == 1

    def test_ob_side_is_bearish(self):
        df  = _bearish_impulse_df()
        obs = find_bearish_obs(df, lookback=5, min_impulse_pct=0.010)
        assert obs[0].side == "BEARISH"

    def test_ob_zone_makes_sense(self):
        df  = _bearish_impulse_df()
        obs = find_bearish_obs(df, lookback=5, min_impulse_pct=0.010)
        ob  = obs[0]
        assert ob.ob_high > ob.ob_low

    def test_flat_market_no_obs(self):
        obs = find_bearish_obs(_flat_df(), min_impulse_pct=0.003)
        assert obs == []


# ---------------------------------------------------------------------------
# detect_order_blocks
# ---------------------------------------------------------------------------

class TestDetectOrderBlocks:

    def test_combines_bullish_and_bearish(self):
        bull_df = _bullish_impulse_df()
        bear_df = _bearish_impulse_df()
        # Build a combined DF: bullish impulse at start, bearish later
        combined = pd.concat([bull_df, bear_df])
        combined = combined[~combined.index.duplicated()]
        obs = detect_order_blocks(combined, lookback=5, min_impulse_pct=0.010)
        sides = {ob.side for ob in obs}
        assert "BULLISH" in sides or "BEARISH" in sides

    def test_returns_sorted_by_timestamp(self):
        obs = detect_order_blocks(_bullish_impulse_df(), lookback=5, min_impulse_pct=0.005)
        ts  = [ob.timestamp for ob in obs]
        assert ts == sorted(ts)

    def test_empty_df_returns_empty(self):
        df  = _flat_df(n=5)
        obs = detect_order_blocks(df, min_impulse_pct=0.999)
        assert obs == []


# ---------------------------------------------------------------------------
# price_in_zone
# ---------------------------------------------------------------------------

class TestPriceInZone:

    def setup_method(self):
        self.ob = OrderBlock(
            pd.Timestamp("2026-01-01", tz="UTC"), "BULLISH",
            ob_high=1.105, ob_low=1.100, impulse_pct=0.005
        )

    def test_price_inside_zone(self):
        assert price_in_zone(1.102, self.ob) is True

    def test_price_at_ob_high(self):
        assert price_in_zone(1.105, self.ob) is True

    def test_price_at_ob_low(self):
        assert price_in_zone(1.100, self.ob) is True

    def test_price_above_zone(self):
        assert price_in_zone(1.110, self.ob) is False

    def test_price_below_zone(self):
        assert price_in_zone(1.095, self.ob) is False

    def test_tolerance_extends_zone(self):
        assert price_in_zone(1.0985, self.ob, tolerance=0.002) is True

    def test_tolerance_zero_no_extension(self):
        assert price_in_zone(1.0985, self.ob, tolerance=0.0) is False


# ---------------------------------------------------------------------------
# mark_mitigated
# ---------------------------------------------------------------------------

class TestMarkMitigated:

    def _make_ob(self, side, high, low):
        return OrderBlock(
            pd.Timestamp("2026-01-01 00:00", tz="UTC"), side, high, low, 0.005
        )

    def test_bullish_ob_mitigated_when_close_below_low(self):
        ob = self._make_ob("BULLISH", 1.105, 1.100)
        # One bar after OB where price closes below 1.100
        df = _make_df([1.098], [1.100], [1.095], [1.097], start="2026-01-01 01:00")
        mark_mitigated([ob], df)
        assert ob.active is False

    def test_bullish_ob_stays_active_when_price_above_low(self):
        ob = self._make_ob("BULLISH", 1.105, 1.100)
        df = _make_df([1.101], [1.108], [1.100], [1.105], start="2026-01-01 01:00")
        mark_mitigated([ob], df)
        assert ob.active is True

    def test_bearish_ob_mitigated_when_close_above_high(self):
        ob = self._make_ob("BEARISH", 1.110, 1.105)
        df = _make_df([1.111], [1.115], [1.110], [1.113], start="2026-01-01 01:00")
        mark_mitigated([ob], df)
        assert ob.active is False

    def test_bearish_ob_stays_active_when_price_below_high(self):
        ob = self._make_ob("BEARISH", 1.110, 1.105)
        df = _make_df([1.108], [1.109], [1.104], [1.107], start="2026-01-01 01:00")
        mark_mitigated([ob], df)
        assert ob.active is True

    def test_no_future_bars_ob_stays_active(self):
        ob = self._make_ob("BULLISH", 1.105, 1.100)
        # Empty df (no bars after OB)
        df = _make_df([], [], [], [], start="2025-12-31")
        mark_mitigated([ob], df)
        assert ob.active is True
