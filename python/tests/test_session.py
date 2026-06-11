"""Phase 18 — Unit tests for signals/session.py.

Tests cover is_in_killzone() boundary conditions, filter_by_session(),
active_session_name(), custom killzone config, and config integration.
"""
from datetime import time
from unittest.mock import MagicMock

import pandas as pd
import pytest

from signals.session import (
    DEFAULT_KILLZONES,
    active_session_name,
    filter_by_session,
    is_in_killzone,
)
from core.config import TradingConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts(hour: int, minute: int = 0, date: str = "2025-06-09") -> pd.Timestamp:
    return pd.Timestamp(f"{date} {hour:02d}:{minute:02d}:00")


def make_signal(hour: int, minute: int = 0) -> MagicMock:
    sig = MagicMock()
    sig.timestamp = ts(hour, minute)
    return sig


# ---------------------------------------------------------------------------
# DEFAULT_KILLZONES sanity check
# ---------------------------------------------------------------------------

class TestDefaultKillzones:
    def test_london_open_defined(self):
        assert "london_open" in DEFAULT_KILLZONES

    def test_new_york_am_defined(self):
        assert "new_york_am" in DEFAULT_KILLZONES

    def test_london_open_window(self):
        start, end = DEFAULT_KILLZONES["london_open"]
        assert start == time(7, 0)
        assert end   == time(10, 0)

    def test_new_york_am_window(self):
        start, end = DEFAULT_KILLZONES["new_york_am"]
        assert start == time(13, 0)
        assert end   == time(16, 0)


# ---------------------------------------------------------------------------
# is_in_killzone — London Open
# ---------------------------------------------------------------------------

class TestIsInKillzoneLondon:
    def test_inside_london_open(self):
        assert is_in_killzone(ts(8, 30)) is True

    def test_london_open_start_inclusive(self):
        assert is_in_killzone(ts(7, 0)) is True

    def test_london_open_end_exclusive(self):
        assert is_in_killzone(ts(10, 0)) is False

    def test_just_before_london_open(self):
        assert is_in_killzone(ts(6, 59)) is False


# ---------------------------------------------------------------------------
# is_in_killzone — New York AM
# ---------------------------------------------------------------------------

class TestIsInKillzoneNewYork:
    def test_inside_ny_am(self):
        assert is_in_killzone(ts(14, 0)) is True

    def test_ny_am_start_inclusive(self):
        assert is_in_killzone(ts(13, 0)) is True

    def test_ny_am_end_exclusive(self):
        assert is_in_killzone(ts(16, 0)) is False

    def test_just_before_ny_am(self):
        assert is_in_killzone(ts(12, 59)) is False


# ---------------------------------------------------------------------------
# is_in_killzone — dead zones
# ---------------------------------------------------------------------------

class TestDeadZones:
    def test_asian_session_excluded(self):
        assert is_in_killzone(ts(3, 0)) is False

    def test_between_sessions_excluded(self):
        assert is_in_killzone(ts(11, 0)) is False

    def test_ny_pm_excluded(self):
        assert is_in_killzone(ts(17, 0)) is False

    def test_midnight_excluded(self):
        assert is_in_killzone(ts(0, 0)) is False


# ---------------------------------------------------------------------------
# is_in_killzone — custom killzones
# ---------------------------------------------------------------------------

class TestCustomKillzones:
    def test_custom_single_window(self):
        custom = {"my_session": (time(9, 0), time(11, 0))}
        assert is_in_killzone(ts(10, 0), killzones=custom) is True
        assert is_in_killzone(ts(8, 0),  killzones=custom) is False

    def test_empty_killzones_always_false(self):
        assert is_in_killzone(ts(8, 0), killzones={}) is False


# ---------------------------------------------------------------------------
# active_session_name
# ---------------------------------------------------------------------------

class TestActiveSessionName:
    def test_london_open_named(self):
        assert active_session_name(ts(8, 0)) == "london_open"

    def test_new_york_am_named(self):
        assert active_session_name(ts(14, 30)) == "new_york_am"

    def test_dead_zone_returns_none(self):
        assert active_session_name(ts(11, 0)) is None

    def test_exact_london_end_returns_none(self):
        assert active_session_name(ts(10, 0)) is None


# ---------------------------------------------------------------------------
# filter_by_session
# ---------------------------------------------------------------------------

class TestFilterBySession:
    def test_keeps_signals_in_session(self):
        signals = [make_signal(8), make_signal(14)]
        result  = filter_by_session(signals)
        assert len(result) == 2

    def test_drops_signals_outside_session(self):
        signals = [make_signal(3), make_signal(11), make_signal(17)]
        result  = filter_by_session(signals)
        assert len(result) == 0

    def test_mixed_signals(self):
        signals = [make_signal(8), make_signal(11), make_signal(14), make_signal(3)]
        result  = filter_by_session(signals)
        assert len(result) == 2

    def test_empty_input(self):
        assert filter_by_session([]) == []

    def test_custom_killzones_applied(self):
        custom  = {"test": (time(10, 0), time(12, 0))}
        signals = [make_signal(11), make_signal(8)]
        result  = filter_by_session(signals, killzones=custom)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TradingConfig integration
# ---------------------------------------------------------------------------

class TestConfigIntegration:
    def test_session_filter_enabled_by_default(self):
        cfg = TradingConfig()
        assert cfg.session_filter_enabled is True

    def test_session_filter_can_be_disabled(self):
        cfg = TradingConfig(session_filter_enabled=False)
        assert cfg.session_filter_enabled is False

    def test_from_env_default_enabled(self, monkeypatch):
        monkeypatch.delenv("SESSION_FILTER_ENABLED", raising=False)
        cfg = TradingConfig.from_env()
        assert cfg.session_filter_enabled is True

    def test_from_env_can_disable(self, monkeypatch):
        monkeypatch.setenv("SESSION_FILTER_ENABLED", "false")
        cfg = TradingConfig.from_env()
        assert cfg.session_filter_enabled is False
