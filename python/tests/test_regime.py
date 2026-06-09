"""
python/tests/test_regime.py

Unit tests for signals/regime.py.

Uses synthetic price series designed to produce each regime clearly.
No external data, no network calls, no mocking required.

Synthetic data patterns:
  - TRENDING_UP:   strong linear uptrend, small noise → high ADX, MA slope +1
  - TRENDING_DOWN: strong linear downtrend, small noise → high ADX, MA slope -1
  - RANGING:       sine-wave oscillation around mean → low ADX, normal ATR
  - VOLATILE:      ranging base + sudden ATR spike → low ADX, high ATR ratio

Minimum bars: 200 (enough for all indicators to warm up fully).
"""

import numpy as np
import pandas as pd
import pytest

from signals.regime import (
    RegimeLabel,
    compute_atr,
    compute_adx,
    compute_ma_slope,
    classify_regime,
    current_regime,
)


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_df(close: np.ndarray, spread: float = 0.001) -> pd.DataFrame:
    """Wrap a close array into a minimal OHLCV DataFrame with UTC index."""
    n   = len(close)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC", name="datetime")
    return pd.DataFrame({
        "open":   close - spread * 0.5,
        "high":   close + spread,
        "low":    close - spread,
        "close":  close,
        "volume": np.ones(n) * 1000.0,
    }, index=idx)


def _trending_up(n: int = 300) -> pd.DataFrame:
    """Strong uptrend: price rises 10 pips/bar with tiny Gaussian noise."""
    rng   = np.random.default_rng(42)
    close = 1.10 + np.arange(n) * 0.0001 + rng.normal(0, 0.00002, n)
    return _make_df(close, spread=0.0002)


def _trending_down(n: int = 300) -> pd.DataFrame:
    """Strong downtrend: price falls 10 pips/bar with tiny Gaussian noise."""
    rng   = np.random.default_rng(42)
    close = 1.20 - np.arange(n) * 0.0001 + rng.normal(0, 0.00002, n)
    return _make_df(close, spread=0.0002)


def _ranging(n: int = 300) -> pd.DataFrame:
    """Sideways market: Ornstein-Uhlenbeck mean-reverting process.

    Direction changes randomly, keeping ADX low — unlike a sine wave which
    has half-cycle trends that push ADX above 25.
    """
    rng   = np.random.default_rng(42)
    close = np.zeros(n)
    close[0] = 1.10
    mean_price = 1.10
    for i in range(1, n):
        # Strong mean reversion (theta=0.4) + small noise
        close[i] = close[i - 1] + 0.4 * (mean_price - close[i - 1]) + rng.normal(0, 0.0002)
    return _make_df(close, spread=0.0003)


def _volatile(n: int = 300) -> pd.DataFrame:
    """First 200 bars very calm (tiny ATR), last 100 bars with massive wicks.

    The calm period anchors the 75th-percentile vol threshold low, so the
    spike period reliably triggers VOLATILE classification.
    """
    calm_n  = 200
    spike_n = n - calm_n
    rng     = np.random.default_rng(7)

    # Calm: price barely moves, tiny high-low range
    calm_close = np.ones(calm_n) * 1.10
    calm_high  = calm_close + 0.00005
    calm_low   = calm_close - 0.00005

    # Spiky: close stays flat but wicks are 100× larger than calm ATR
    spike_close = np.ones(spike_n) * 1.10
    spike_high  = spike_close + rng.uniform(0.008, 0.015, spike_n)
    spike_low   = spike_close - rng.uniform(0.008, 0.015, spike_n)

    close  = np.concatenate([calm_close,  spike_close])
    high   = np.concatenate([calm_high,   spike_high])
    low    = np.concatenate([calm_low,    spike_low])
    idx    = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC", name="datetime")
    return pd.DataFrame({
        "open":   close - 0.00005,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": np.ones(n) * 1000.0,
    }, index=idx)


def _short_df(n: int = 10) -> pd.DataFrame:
    """Too few bars for indicators to warm up."""
    close = np.ones(n) * 1.10
    return _make_df(close)


# ---------------------------------------------------------------------------
# RegimeLabel
# ---------------------------------------------------------------------------

class TestRegimeLabel:

    def test_str_comparison(self):
        assert RegimeLabel.TRENDING_UP == "TRENDING_UP"

    def test_all_labels_exist(self):
        labels = {r.value for r in RegimeLabel}
        assert labels == {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "UNKNOWN"}

    def test_serialises_to_string(self):
        assert str(RegimeLabel.RANGING) == "RANGING"


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------

class TestComputeATR:

    def test_returns_series_same_length(self):
        df  = _trending_up()
        atr = compute_atr(df)
        assert len(atr) == len(df)

    def test_values_positive_after_warmup(self):
        df  = _trending_up()
        atr = compute_atr(df, period=14)
        assert (atr.dropna() > 0).all()

    def test_volatile_has_higher_atr_than_trending(self):
        atr_trend = compute_atr(_trending_up()).iloc[-1]
        atr_vol   = compute_atr(_volatile()).iloc[-1]
        assert atr_vol > atr_trend

    def test_flat_market_has_small_atr(self):
        n     = 100
        close = np.ones(n) * 1.10
        df    = _make_df(close, spread=0.00001)
        atr   = compute_atr(df, period=14)
        assert atr.dropna().iloc[-1] < 0.001

    def test_custom_period(self):
        df   = _trending_up()
        atr7 = compute_atr(df, period=7)
        assert len(atr7) == len(df)


# ---------------------------------------------------------------------------
# compute_adx
# ---------------------------------------------------------------------------

class TestComputeADX:

    def test_returns_series_same_length(self):
        df  = _trending_up()
        adx = compute_adx(df)
        assert len(adx) == len(df)

    def test_trending_adx_above_25(self):
        df  = _trending_up(300)
        adx = compute_adx(df, period=14)
        # Last 100 bars should be clearly trending
        assert adx.iloc[-100:].dropna().mean() > 25

    def test_ranging_adx_below_25(self):
        df  = _ranging(300)
        adx = compute_adx(df, period=14)
        assert adx.iloc[-100:].dropna().mean() < 25

    def test_adx_bounded_0_to_100(self):
        df  = _trending_up()
        adx = compute_adx(df).dropna()
        assert (adx >= 0).all() and (adx <= 100).all()

    def test_trending_up_and_down_similar_adx(self):
        """ADX measures strength not direction — up and down trends ≈ same ADX."""
        adx_up   = compute_adx(_trending_up(300)).iloc[-50:].mean()
        adx_down = compute_adx(_trending_down(300)).iloc[-50:].mean()
        assert abs(adx_up - adx_down) < 20   # both trending, roughly similar


# ---------------------------------------------------------------------------
# compute_ma_slope
# ---------------------------------------------------------------------------

class TestComputeMASlope:

    def test_returns_series_same_length(self):
        df = _trending_up()
        assert len(compute_ma_slope(df)) == len(df)

    def test_uptrend_slope_positive(self):
        df    = _trending_up(300)
        slope = compute_ma_slope(df, period=20)
        # Majority of later bars should be +1
        assert (slope.iloc[-100:].dropna() >= 0).mean() > 0.8

    def test_downtrend_slope_negative(self):
        df    = _trending_down(300)
        slope = compute_ma_slope(df, period=20)
        assert (slope.iloc[-100:].dropna() <= 0).mean() > 0.8

    def test_values_only_minus1_0_plus1(self):
        df    = _ranging()
        slope = compute_ma_slope(df).dropna()
        assert set(slope.unique()).issubset({-1.0, 0.0, 1.0})


# ---------------------------------------------------------------------------
# classify_regime
# ---------------------------------------------------------------------------

class TestClassifyRegime:

    def test_returns_series_same_length(self):
        df      = _trending_up()
        regimes = classify_regime(df)
        assert len(regimes) == len(df)

    def test_trending_up_detected(self):
        df      = _trending_up(300)
        regimes = classify_regime(df)
        tail    = regimes.iloc[-50:]
        assert (tail == RegimeLabel.TRENDING_UP).mean() > 0.7

    def test_trending_down_detected(self):
        df      = _trending_down(300)
        regimes = classify_regime(df)
        tail    = regimes.iloc[-50:]
        assert (tail == RegimeLabel.TRENDING_DOWN).mean() > 0.7

    def test_ranging_detected(self):
        """A mean-reverting market must not be classified as trending.

        RANGING or VOLATILE are both valid non-trending labels.
        We assert < 20% trending bars rather than demanding pure RANGING,
        because the OU process naturally produces some high-ATR bars.
        """
        df      = _ranging(300)
        regimes = classify_regime(df)
        tail    = regimes.iloc[-50:]
        is_trending = tail.isin([RegimeLabel.TRENDING_UP, RegimeLabel.TRENDING_DOWN])
        assert is_trending.mean() < 0.2

    def test_volatile_detected(self):
        df      = _volatile(300)
        regimes = classify_regime(df)
        tail    = regimes.iloc[-50:]
        assert (tail == RegimeLabel.VOLATILE).mean() > 0.5

    def test_short_df_all_unknown(self):
        df      = _short_df(10)
        regimes = classify_regime(df)
        assert (regimes == RegimeLabel.UNKNOWN).all()

    def test_early_bars_are_unknown(self):
        df      = _trending_up(300)
        regimes = classify_regime(df)
        # First ~14 bars can't have valid ADX
        assert regimes.iloc[:5].eq(RegimeLabel.UNKNOWN).all()

    def test_custom_adx_threshold(self):
        """High threshold makes trending harder to classify."""
        df      = _ranging(300)
        default = classify_regime(df)
        strict  = classify_regime(df, adx_trend_threshold=50.0)
        # With a very high threshold, even more bars should be non-trending
        trending_default = (default.isin([RegimeLabel.TRENDING_UP, RegimeLabel.TRENDING_DOWN])).sum()
        trending_strict  = (strict.isin([RegimeLabel.TRENDING_UP, RegimeLabel.TRENDING_DOWN])).sum()
        assert trending_strict <= trending_default

    def test_all_values_are_regime_labels(self):
        df      = _trending_up()
        regimes = classify_regime(df)
        valid   = set(r.value for r in RegimeLabel)
        assert set(regimes.unique()).issubset(valid)


# ---------------------------------------------------------------------------
# current_regime
# ---------------------------------------------------------------------------

class TestCurrentRegime:

    def test_trending_up_returns_trending_up(self):
        assert current_regime(_trending_up(300)) == RegimeLabel.TRENDING_UP

    def test_trending_down_returns_trending_down(self):
        assert current_regime(_trending_down(300)) == RegimeLabel.TRENDING_DOWN

    def test_too_short_returns_unknown(self):
        assert current_regime(_short_df(5)) == RegimeLabel.UNKNOWN

    def test_returns_regime_label_instance(self):
        result = current_regime(_trending_up(300))
        assert isinstance(result, RegimeLabel)

    def test_string_comparison_works(self):
        result = current_regime(_trending_up(300))
        assert result == "TRENDING_UP"
