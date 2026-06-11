"""Tests for run.py — entry point utilities.

Tests cover ensure_dirs() directory creation and preflight() validation
logic. The main() entry point and logging I/O are not unit-tested
(they start an infinite loop and write to disk) — integration coverage
comes from manual paper trade runs.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.config import TradingConfig
from run import ensure_dirs, preflight


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> TradingConfig:
    defaults = dict(
        account_equity=10_000.0,
        risk_pct_per_trade=1.0,
        contract_size=100_000.0,
    )
    defaults.update(kwargs)
    return TradingConfig(**defaults)


# ---------------------------------------------------------------------------
# ensure_dirs
# ---------------------------------------------------------------------------

class TestEnsureDirs:

    def test_creates_bridge_folder(self, tmp_path):
        cfg = make_config(
            bridge_folder=str(tmp_path / "bridge/out"),
            feedback_folder=str(tmp_path / "bridge/in"),
            data_dir=str(tmp_path / "data"),
        )
        ensure_dirs(cfg)
        assert Path(cfg.bridge_folder).is_dir()

    def test_creates_feedback_folder(self, tmp_path):
        cfg = make_config(
            bridge_folder=str(tmp_path / "bridge/out"),
            feedback_folder=str(tmp_path / "bridge/in"),
            data_dir=str(tmp_path / "data"),
        )
        ensure_dirs(cfg)
        assert Path(cfg.feedback_folder).is_dir()

    def test_creates_data_dir(self, tmp_path):
        cfg = make_config(
            bridge_folder=str(tmp_path / "bridge/out"),
            feedback_folder=str(tmp_path / "bridge/in"),
            data_dir=str(tmp_path / "data/csv"),
        )
        ensure_dirs(cfg)
        assert Path(cfg.data_dir).is_dir()

    def test_nested_paths_created(self, tmp_path):
        deep = str(tmp_path / "a/b/c/d")
        cfg = make_config(
            bridge_folder=deep,
            feedback_folder=str(tmp_path / "fb"),
            data_dir=str(tmp_path / "dt"),
        )
        ensure_dirs(cfg)
        assert Path(deep).is_dir()

    def test_existing_dirs_not_raised(self, tmp_path):
        cfg = make_config(
            bridge_folder=str(tmp_path),
            feedback_folder=str(tmp_path),
            data_dir=str(tmp_path),
        )
        # Should not raise even though dirs already exist
        ensure_dirs(cfg)
        ensure_dirs(cfg)


# ---------------------------------------------------------------------------
# preflight — pass cases
# ---------------------------------------------------------------------------

class TestPreflightPass:

    def _logger(self) -> logging.Logger:
        return logging.getLogger("test_run")

    def test_valid_config_returns_true(self):
        cfg = make_config()
        assert preflight(cfg, self._logger()) is True

    def test_risk_pct_at_boundary_10_passes(self):
        cfg = make_config(risk_pct_per_trade=10.0)
        assert preflight(cfg, self._logger()) is True

    def test_risk_pct_small_positive_passes(self):
        cfg = make_config(risk_pct_per_trade=0.1)
        assert preflight(cfg, self._logger()) is True

    def test_minimal_single_symbol_passes(self):
        cfg = make_config(symbols=["EURUSDm"])
        assert preflight(cfg, self._logger()) is True

    def test_gold_contract_size_passes(self):
        cfg = make_config(contract_size=100.0)
        assert preflight(cfg, self._logger()) is True


# ---------------------------------------------------------------------------
# preflight — fail cases
# ---------------------------------------------------------------------------

class TestPreflightFail:

    def _logger(self) -> logging.Logger:
        return logging.getLogger("test_run_fail")

    def test_empty_symbols_returns_false(self):
        cfg = make_config(symbols=[])
        assert preflight(cfg, self._logger()) is False

    def test_zero_equity_returns_false(self):
        cfg = make_config(account_equity=0.0)
        assert preflight(cfg, self._logger()) is False

    def test_negative_equity_returns_false(self):
        cfg = make_config(account_equity=-500.0)
        assert preflight(cfg, self._logger()) is False

    def test_zero_risk_pct_returns_false(self):
        cfg = make_config(risk_pct_per_trade=0.0)
        assert preflight(cfg, self._logger()) is False

    def test_risk_pct_above_10_returns_false(self):
        cfg = make_config(risk_pct_per_trade=10.1)
        assert preflight(cfg, self._logger()) is False

    def test_zero_contract_size_returns_false(self):
        cfg = make_config(contract_size=0.0)
        assert preflight(cfg, self._logger()) is False

    def test_negative_contract_size_returns_false(self):
        cfg = make_config(contract_size=-100.0)
        assert preflight(cfg, self._logger()) is False

    def test_zero_poll_interval_returns_false(self):
        cfg = make_config(poll_interval=0)
        assert preflight(cfg, self._logger()) is False

    def test_multiple_errors_all_logged(self, caplog):
        cfg = make_config(account_equity=0.0, risk_pct_per_trade=0.0)
        with caplog.at_level(logging.ERROR, logger="test_run_fail"):
            result = preflight(cfg, self._logger())
        assert result is False
        error_lines = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_lines) >= 2
