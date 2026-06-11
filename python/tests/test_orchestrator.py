"""
python/tests/test_orchestrator.py

Unit tests for the orchestrator (Phase 15).
All external module calls are mocked — no network, no file I/O.
"""

import os
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from core.config import TradingConfig
from core.orchestrator import Orchestrator
from signals.regime import RegimeLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_df(n: int = 100):
    """Minimal OHLCV DataFrame for tests."""
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 1000.0},
        index=idx,
    )


def make_regimes(df):
    """All bars TRENDING_UP."""
    return pd.Series([RegimeLabel.TRENDING_UP] * len(df), index=df.index)


def make_signal(symbol="EURUSDm", comment="OB_BUY_test"):
    """Create a minimal mock signal matching TradeSignal's interface."""
    sig = MagicMock()
    sig.symbol = symbol
    sig.side = "BUY"
    sig.size = 0.01
    sig.stop_loss = 1.09
    sig.take_profit = 1.13
    sig.comment = comment
    # Phase 18: must be a real pd.Timestamp so the session filter can call .time()
    # 08:30 UTC falls inside the London Open killzone (07:00–10:00)
    sig.timestamp = pd.Timestamp("2026-01-05 08:30:00")
    return sig


# Patch targets — all inside core.orchestrator where they are imported
PATCH_UPDATE = "core.orchestrator.update_ohlcv"
PATCH_REGIME = "core.orchestrator.classify_regime"
PATCH_OBS    = "core.orchestrator.detect_order_blocks"
PATCH_MIT    = "core.orchestrator.mark_mitigated"
PATCH_GEN    = "core.orchestrator.generate_signals"
PATCH_EXEC   = "core.orchestrator.execute_signals"


# ---------------------------------------------------------------------------
# TradingConfig tests
# ---------------------------------------------------------------------------

class TestTradingConfig:

    def test_default_symbols(self):
        cfg = TradingConfig()
        assert cfg.symbols == ["EURUSDm", "XAUUSDm"]

    def test_default_lot_size(self):
        cfg = TradingConfig()
        assert cfg.lot_size == 0.01

    def test_default_poll_interval(self):
        cfg = TradingConfig()
        assert cfg.poll_interval == 60

    def test_default_timeframe(self):
        cfg = TradingConfig()
        assert cfg.timeframe == "H1"

    def test_default_bridge_folder(self):
        cfg = TradingConfig()
        assert cfg.bridge_folder == "bridge/outgoing"

    def test_from_env_reads_symbols(self, monkeypatch):
        monkeypatch.setenv("SYMBOLS", "GBPUSD,USDJPY")
        cfg = TradingConfig.from_env()
        assert cfg.symbols == ["GBPUSD", "USDJPY"]

    def test_from_env_reads_bridge_folder(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_FOLDER", "/custom/path")
        cfg = TradingConfig.from_env()
        assert cfg.bridge_folder == "/custom/path"

    def test_from_env_defaults_when_absent(self):
        # Ensure no env vars set for trading config
        for key in ("SYMBOLS", "BRIDGE_FOLDER", "LOT_SIZE"):
            os.environ.pop(key, None)
        cfg = TradingConfig.from_env()
        assert cfg.symbols == ["EURUSDm", "XAUUSDm"]
        assert cfg.lot_size == 0.01


# ---------------------------------------------------------------------------
# Orchestrator initialization
# ---------------------------------------------------------------------------

class TestOrchestratorInit:

    def test_risk_manager_uses_config_values(self):
        cfg = TradingConfig(max_open_trades=3, max_lot_per_symbol=0.5)
        orch = Orchestrator(cfg)
        assert orch.risk_manager.max_open_trades == 3
        assert orch.risk_manager.max_lot_per_symbol == 0.5

    def test_open_positions_starts_empty(self):
        orch = Orchestrator(TradingConfig())
        assert orch.open_positions == []

    def test_dispatched_ids_starts_empty(self):
        orch = Orchestrator(TradingConfig())
        assert len(orch._dispatched_ids) == 0


# ---------------------------------------------------------------------------
# run_cycle() happy path
# ---------------------------------------------------------------------------

class TestRunCycleHappyPath:

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_returns_dict_keyed_by_symbol(self, mock_update, mock_regime,
                                          mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = [make_signal()]

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert "EURUSDm" in summary

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_summary_contains_expected_keys(self, mock_update, mock_regime,
                                             mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = []

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert "signals_generated" in summary["EURUSDm"]
        assert "signals_approved" in summary["EURUSDm"]
        assert "signals_dispatched" in summary["EURUSDm"]
        assert "regime" in summary["EURUSDm"]
        assert "ob_count" in summary["EURUSDm"]

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_signals_generated_count(self, mock_update, mock_regime,
                                      mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = [make_signal(), make_signal(comment="sig2")]

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert summary["EURUSDm"]["signals_generated"] == 2

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_update_ohlcv_called_once_per_symbol(self, mock_update, mock_regime,
                                                  mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = []

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        orch.run_cycle()

        mock_update.assert_called_once()

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_generate_signals_called_once_per_symbol(self, mock_update, mock_regime,
                                                      mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = []

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        orch.run_cycle()

        mock_gen.assert_called_once()


# ---------------------------------------------------------------------------
# Signal dispatch
# ---------------------------------------------------------------------------

class TestSignalDispatch:

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_approved_signal_dispatched(self, mock_update, mock_regime,
                                         mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = [make_signal()]

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert summary["EURUSDm"]["signals_dispatched"] == 1
        mock_exec.assert_called_once()

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_rejected_signal_not_dispatched(self, mock_update, mock_regime,
                                             mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        mock_gen.return_value = [make_signal()]

        # Set max_open_trades=0 so everything is rejected
        cfg = TradingConfig(symbols=["EURUSDm"], max_open_trades=0)
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert summary["EURUSDm"]["signals_approved"] == 0
        assert summary["EURUSDm"]["signals_dispatched"] == 0
        mock_exec.assert_not_called()

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN)
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_duplicate_signal_skipped(self, mock_update, mock_regime,
                                       mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)
        # Same comment = same dedup key
        mock_gen.return_value = [make_signal(comment="dup"), make_signal(comment="dup")]

        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        # Both approved by risk manager, but second is deduped
        assert summary["EURUSDm"]["signals_approved"] == 2
        assert summary["EURUSDm"]["signals_dispatched"] == 1


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

class TestErrorIsolation:

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN, return_value=[])
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_error_in_one_symbol_doesnt_crash_others(self, mock_update, mock_regime,
                                                      mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()

        # First symbol raises, second succeeds
        def side_effect_update(data_dir, symbol, tf, lookback):
            if symbol == "BAD":
                raise RuntimeError("network error")
            return df

        mock_update.side_effect = side_effect_update
        mock_regime.return_value = make_regimes(df)

        cfg = TradingConfig(symbols=["BAD", "GOOD"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        # Both symbols have keys in the summary
        assert "BAD" in summary
        assert "GOOD" in summary
        # BAD has zeros (error path)
        assert summary["BAD"]["signals_generated"] == 0
        # GOOD processed normally
        assert summary["GOOD"]["regime"] == "TRENDING_UP"

    @patch(PATCH_UPDATE, side_effect=RuntimeError("crash"))
    def test_exception_does_not_propagate(self, mock_update):
        cfg = TradingConfig(symbols=["EURUSDm"])
        orch = Orchestrator(cfg)
        # Should not raise
        summary = orch.run_cycle()
        assert "EURUSDm" in summary
        assert summary["EURUSDm"]["signals_generated"] == 0


# ---------------------------------------------------------------------------
# Multi-symbol
# ---------------------------------------------------------------------------

class TestMultiSymbol:

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN, return_value=[])
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_update_called_per_symbol(self, mock_update, mock_regime,
                                       mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)

        cfg = TradingConfig(symbols=["EURUSDm", "XAUUSDm"])
        orch = Orchestrator(cfg)
        orch.run_cycle()

        assert mock_update.call_count == 2

    @patch(PATCH_EXEC, return_value=["/tmp/test.txt"])
    @patch(PATCH_GEN, return_value=[])
    @patch(PATCH_MIT, side_effect=lambda obs, df: obs)
    @patch(PATCH_OBS, return_value=[])
    @patch(PATCH_REGIME)
    @patch(PATCH_UPDATE)
    def test_both_symbols_in_summary(self, mock_update, mock_regime,
                                      mock_obs, mock_mit, mock_gen, mock_exec):
        df = make_df()
        mock_update.return_value = df
        mock_regime.return_value = make_regimes(df)

        cfg = TradingConfig(symbols=["EURUSDm", "XAUUSDm"])
        orch = Orchestrator(cfg)
        summary = orch.run_cycle()

        assert "EURUSDm" in summary
        assert "XAUUSDm" in summary
