"""
Unit tests for risk management module.
"""
import pytest
import numpy as np
from risk.manager import RiskManager, RiskLimits, PositionInfo


class TestRiskLimits:
    """Tests for RiskLimits dataclass."""

    def test_default_values(self):
        limits = RiskLimits()
        assert limits.max_position_pct == 0.3
        assert limits.max_total_position_pct == 0.8
        assert limits.max_daily_loss_pct == 0.05
        assert limits.max_consecutive_losses == 3
        assert limits.stop_loss_pct == 0.05
        assert limits.take_profit_pct == 0.10

    def test_custom_values(self):
        limits = RiskLimits(
            max_position_pct=0.2,
            max_total_position_pct=0.6,
            max_daily_loss_pct=0.03,
            max_consecutive_losses=5,
            stop_loss_pct=0.03,
            take_profit_pct=0.08,
            position_sizing="kelly",
        )
        assert limits.max_position_pct == 0.2
        assert limits.position_sizing == "kelly"


class TestPositionInfo:
    """Tests for PositionInfo dataclass."""

    def test_required_fields(self):
        pos = PositionInfo(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=65000.0,
            quantity=0.1,
            leverage=3,
        )
        assert pos.symbol == "BTCUSDT"
        assert pos.side == "LONG"
        assert pos.entry_price == 65000.0
        assert pos.quantity == 0.1
        assert pos.leverage == 3

    def test_short_position(self):
        pos = PositionInfo(
            symbol="ETHUSDT",
            side="SHORT",
            entry_price=3000.0,
            quantity=1.0,
            leverage=5,
            stop_loss=3150.0,
            take_profit=2850.0,
        )
        assert pos.side == "SHORT"
        assert pos.stop_loss == 3150.0
        assert pos.take_profit == 2850.0


class TestRiskManager:
    """Tests for RiskManager."""

    def _make_manager(self, **kwargs):
        limits = RiskLimits(**kwargs)
        return RiskManager(limits=limits)

    def test_can_open_position_no_existing(self):
        rm = self._make_manager()
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert allowed
        assert reason == "OK"

    def test_cannot_open_when_paused(self):
        rm = self._make_manager()
        rm.pause_trading("test pause")
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert not allowed

    def test_resume_after_pause(self):
        rm = self._make_manager()
        rm.pause_trading("test")
        rm.resume_trading()
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert allowed

    def test_already_has_position(self):
        rm = self._make_manager()
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert not allowed

    def test_total_exposure_limit(self):
        rm = self._make_manager(max_total_position_pct=0.1)
        # Open a position that uses > 10% of capital
        rm.set_capital(10000.0)
        rm.open_position("ETHUSDT", "LONG", 3000.0, 1.0, leverage=3)
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert not allowed

    def test_position_sizing_fixed(self):
        rm = self._make_manager(position_sizing="fixed", max_position_pct=0.3)
        rm.set_capital(10000.0)
        size = rm.calculate_position_size("BTCUSDT", 65000.0, leverage=3)
        expected = (10000 * 0.3) * 3 / 65000.0
        assert abs(size - expected) < 1e-8

    def test_position_sizing_kelly(self):
        rm = self._make_manager(position_sizing="kelly")
        rm.set_capital(10000.0)
        size = rm.calculate_position_size("BTCUSDT", 65000.0, leverage=3)
        assert size > 0

    def test_stop_loss_long(self):
        rm = self._make_manager(stop_loss_pct=0.05)
        sl = rm.calculate_stop_loss(65000.0, "LONG")
        assert sl == 65000.0 * 0.95

    def test_stop_loss_short(self):
        rm = self._make_manager(stop_loss_pct=0.05)
        sl = rm.calculate_stop_loss(65000.0, "SHORT")
        assert sl == 65000.0 * 1.05

    def test_take_profit_long(self):
        rm = self._make_manager(take_profit_pct=0.10)
        tp = rm.calculate_take_profit(65000.0, "LONG")
        assert tp == 65000.0 * 1.10

    def test_take_profit_short(self):
        rm = self._make_manager(take_profit_pct=0.10)
        tp = rm.calculate_take_profit(65000.0, "SHORT")
        assert tp == 65000.0 * 0.90

    def test_check_stop_loss_triggered(self):
        rm = self._make_manager(stop_loss_pct=0.05)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        # Price drops below stop loss (65000 * 0.95 = 61750)
        assert rm.check_stop_conditions("BTCUSDT", 61000.0)

    def test_check_stop_loss_not_triggered(self):
        rm = self._make_manager(stop_loss_pct=0.05)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        assert not rm.check_stop_conditions("BTCUSDT", 64000.0)

    def test_take_profit_triggered(self):
        rm = self._make_manager(take_profit_pct=0.10)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        # Price rises above take profit (65000 * 1.10 = 71500)
        assert rm.check_stop_conditions("BTCUSDT", 72000.0)

    def test_consecutive_losses(self):
        rm = self._make_manager(max_consecutive_losses=2)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        rm.close_position("BTCUSDT", 64000.0)  # loss
        rm.open_position("ETHUSDT", "LONG", 3000.0, 0.1, leverage=3)
        rm.close_position("ETHUSDT", 2900.0)  # loss
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert not allowed

    def test_consecutive_losses_reset_on_win(self):
        rm = self._make_manager(max_consecutive_losses=2)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        rm.close_position("BTCUSDT", 64000.0)  # loss
        rm.open_position("ETHUSDT", "LONG", 3000.0, 0.1, leverage=3)
        rm.close_position("ETHUSDT", 3100.0)  # win resets
        allowed, reason = rm.can_open_position("BTCUSDT", "LONG")
        assert allowed

    def test_close_position_returns_pnl(self):
        rm = self._make_manager()
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        pnl = rm.close_position("BTCUSDT", 66000.0)
        assert pnl is not None
        assert pnl > 0

    def test_close_nonexistent_position(self):
        rm = self._make_manager()
        pnl = rm.close_position("BTCUSDT", 65000.0)
        assert pnl is None

    def test_risk_summary(self):
        rm = self._make_manager()
        rm.set_capital(10000.0)
        rm.open_position("BTCUSDT", "LONG", 65000.0, 0.1, leverage=3)
        summary = rm.get_risk_summary()
        assert summary['total_capital'] == 10000.0
        assert summary['open_positions'] == 1
        assert 'positions' in summary

    def test_kelly_dynamic(self):
        """Kelly formula should work with defaults when no trade history exists."""
        rm = self._make_manager(position_sizing="kelly")
        rm.set_capital(10000.0)
        size = rm.calculate_position_size("BTCUSDT", 65000.0, leverage=3)

        # With defaults (win_rate=0.5, reward_risk=1.5):
        # kelly_pct = 0.5 - (1-0.5)/1.5 = 0.5 - 0.333 = 0.167
        # capped to [0.02, 0.25] -> 0.167
        expected_kelly = 0.5 - (1 - 0.5) / 1.5  # ~0.1667
        expected_kelly = max(0.02, min(expected_kelly, 0.25))
        expected_size = (10000.0 * expected_kelly) * 3 / 65000.0
        assert abs(size - expected_size) < 1e-8, f"size={size}, expected={expected_size}"
        assert size > 0

        # Also test that the result is within reasonable bounds
        assert size > 0.001  # minimum quantity
        assert size < 0.2   # reasonable upper bound for 10k capital
