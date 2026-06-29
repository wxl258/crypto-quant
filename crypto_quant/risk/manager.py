"""
Risk Manager - Position sizing and risk control.

Provides risk limits configuration, position tracking, and a RiskManager
class for managing trading risk including position sizing calculations,
stop-loss/take-profit levels, and trading pause/resume logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from config import get_timezone

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(get_timezone())


@dataclass
class RiskLimits:
    """Risk control limits.

    Attributes:
        max_position_pct: Maximum fraction of capital per single position (0-1).
        max_total_position_pct: Maximum fraction of capital across all positions (0-1).
        max_daily_loss_pct: Maximum allowed daily loss as fraction of capital (0-1).
        max_consecutive_losses: Max consecutive losing trades before pausing.
        stop_loss_pct: Default stop-loss percentage (0-1).
        take_profit_pct: Default take-profit percentage (0-1).
        position_sizing: Position sizing method (fixed, kelly, atr).
    """
    max_position_pct: float = 0.3
    max_total_position_pct: float = 0.8
    max_daily_loss_pct: float = 0.05
    max_consecutive_losses: int = 3
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    position_sizing: str = "fixed"  # fixed, kelly, atr


@dataclass
class PositionInfo:
    """Current position information.

    Attributes:
        symbol: Trading pair symbol (e.g., BTCUSDT).
        side: Position direction (LONG or SHORT).
        entry_price: Average entry price.
        quantity: Position size in base asset units.
        leverage: Leverage multiplier.
        stop_loss: Stop-loss trigger price.
        take_profit: Take-profit trigger price.
    """
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float = 0.0
    take_profit: float = 0.0


class RiskManager:
    """Manages trading risk including position sizing and risk checks.

    Tracks open positions, daily PnL, consecutive losses, and supports
    trading pause/resume based on risk limit violations.
    """

    def __init__(
        self,
        limits: RiskLimits | dict[str, Any] | None = None,
        initial_capital: float = 10000.0,
    ) -> None:
        """Initialize the RiskManager.

        Args:
            limits: RiskLimits instance or dict of risk parameters.
                If None, default RiskLimits are used.
            initial_capital: Starting capital in quote currency.
        """
        if limits is None:
            self.limits = RiskLimits()
        elif isinstance(limits, RiskLimits):
            self.limits = limits
        elif isinstance(limits, dict):
            # Only pass keys that match RiskLimits fields
            valid_fields = {f.name for f in __import__('dataclasses').fields(RiskLimits)}
            filtered = {k: v for k, v in limits.items() if k in valid_fields}
            self.limits = RiskLimits(**filtered)
        else:
            self.limits = RiskLimits()
        self.positions: dict[str, PositionInfo] = {}
        self.daily_pnl: dict[date, float] = {}
        self.consecutive_losses: int = 0
        self.total_capital: float = initial_capital
        self.trading_paused: bool = False
        self.pause_reason: str = ""
        self.daily_max_drawdown: float = 0.0
        self.daily_peak_equity: float = 0.0
        self.pause_until: datetime | None = None

    def set_capital(self, capital: float) -> None:
        """Update total capital.

        Args:
            capital: New total capital value in quote currency.
        """
        self.total_capital = capital

    def calculate_position_size(
        self,
        symbol: str,
        price: float,
        leverage: int = 3,
        atr: float | None = None,
    ) -> float:
        """Calculate position size based on risk parameters.

        Supports three sizing methods:
        - fixed: Fixed percentage of capital.
        - kelly: Kelly Criterion using dynamic win_rate and reward_risk from
          trade history (via DataStore), capped between 2% and 25% of capital.
          Falls back to defaults (0.5/1.5) when no trade history exists.
        - atr: Risk 1% of capital per trade based on 2x ATR stop distance.

        Args:
            symbol: Trading pair symbol.
            price: Current price.
            leverage: Leverage multiplier (default 3).
            atr: Average True Range value for ATR-based sizing.

        Returns:
            Quantity in base asset units (minimum 0.001).
        """
        if self.limits.position_sizing == "fixed":
            # Fixed percentage of capital
            position_value = self.total_capital * self.limits.max_position_pct
            quantity = position_value * leverage / price

        elif self.limits.position_sizing == "kelly":
            # Kelly Criterion - dynamically compute win_rate and reward_risk
            # from trade history if available, otherwise use defaults
            win_rate = 0.5
            reward_risk = 1.5
            used_defaults = False

            try:
                from data.store import DataStore
                store = DataStore.get_instance()
                if store is not None:
                    stats = store.get_trade_stats(symbol)
                    total = stats.get('total_trades', 0)
                    if total > 0:
                        # Get avg win/loss from individual trades for reward_risk
                        import sqlite3
                        conn = sqlite3.connect(store.db_path)
                        avg_pnl = conn.execute(
                            "SELECT AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win, "
                            "AVG(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE NULL END) as avg_loss "
                            "FROM trades WHERE status='closed' AND symbol=?",
                            (symbol,)
                        ).fetchone()
                        conn.close()

                        wr = stats.get('win_rate', 0) / 100.0  # convert percentage to decimal
                        if avg_pnl and avg_pnl[0] and avg_pnl[1] and avg_pnl[1] > 0:
                            win_rate = wr
                            reward_risk = avg_pnl[0] / avg_pnl[1]
                        else:
                            win_rate = wr
                            reward_risk = 1.5
                    else:
                        used_defaults = True
                else:
                    used_defaults = True
            except Exception as e:
                logger.debug(f"Cannot fetch trade stats for Kelly: {e}")
                used_defaults = True

            if used_defaults:
                logger.info("Kelly: using default win_rate=0.5, reward_risk=1.5 (no trade history)")

            kelly_pct = win_rate - (1 - win_rate) / reward_risk
            kelly_pct = max(0.02, min(kelly_pct, 0.25))  # Cap at 2%-25%
            position_value = self.total_capital * kelly_pct
            quantity = position_value * leverage / price

        elif self.limits.position_sizing == "atr" and atr is not None:
            # Risk based on ATR - risk 1% of capital per trade
            risk_amount = self.total_capital * 0.01
            stop_distance = atr * 2  # 2 ATR stop
            quantity = risk_amount * leverage / stop_distance
        else:
            logger.warning("ATR mode requires ATR value, falling back to fixed sizing")
            position_value = self.total_capital * self.limits.max_position_pct
            quantity = position_value * leverage / price

        return max(0.001, quantity)  # Minimum quantity

    def calculate_stop_loss(
        self,
        entry_price: float,
        side: str,
        atr: float | None = None,
    ) -> float:
        """Calculate stop loss price.

        Uses either ATR-based distance (2x ATR) or fixed percentage
        from entry price.

        Args:
            entry_price: Position entry price.
            side: Position direction (LONG or SHORT).
            atr: Average True Range for dynamic stop distance.

        Returns:
            Stop-loss trigger price.
        """
        stop_pct = self.limits.stop_loss_pct

        if atr is not None:
            stop_distance = atr * 2
        else:
            stop_distance = entry_price * stop_pct

        if side == "LONG":
            return entry_price - stop_distance
        else:
            return entry_price + stop_distance

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Calculate take profit price.

        Args:
            entry_price: Position entry price.
            side: Position direction (LONG or SHORT).

        Returns:
            Take-profit trigger price.
        """
        tp_pct = self.limits.take_profit_pct
        if side == "LONG":
            return entry_price * (1 + tp_pct)
        else:
            return entry_price * (1 - tp_pct)

    def can_open_position(self, symbol: str, side: str) -> tuple[bool, str]:
        """Check if a new position can be opened.

        Validates against: trading pause state, existing positions,
        total exposure limit, consecutive loss limit, and daily loss limit.

        Args:
            symbol: Trading pair symbol.
            side: Intended position direction (LONG or SHORT).

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        if self.trading_paused:
            if self.pause_until is not None and datetime.now(_TZ) >= self.pause_until:
                self.resume_trading()
            else:
                return False, f"交易暂停: {self.pause_reason}"

        if symbol in self.positions:
            return False, f"{symbol}已有持仓"

        # Check total positions
        total_value = sum(
            p.quantity * p.entry_price / p.leverage
            for p in self.positions.values()
        )
        if total_value / self.total_capital >= self.limits.max_total_position_pct:
            return False, "总仓位已达上限"

        # Check consecutive losses
        if self.consecutive_losses >= self.limits.max_consecutive_losses:
            return False, f"连续亏损{self.consecutive_losses}次，暂停交易"

        # Check daily loss limit using max drawdown
        if self.daily_max_drawdown >= self.total_capital * self.limits.max_daily_loss_pct:
            return False, "日亏损已达上限"

        return True, "OK"

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int = 3,
        atr: float | None = None,
    ) -> None:
        """Record a new position.

        Calculates stop-loss and take-profit levels and stores the
        position information.

        Args:
            symbol: Trading pair symbol.
            side: Position direction (LONG or SHORT).
            entry_price: Entry price.
            quantity: Position size in base asset units.
            leverage: Leverage multiplier (default 3).
            atr: Average True Range for dynamic stop-loss calculation.
        """
        sl = self.calculate_stop_loss(entry_price, side, atr)
        tp = self.calculate_take_profit(entry_price, side)

        self.positions[symbol] = PositionInfo(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=sl,
            take_profit=tp,
        )
        logger.info(f"Opened {side} {symbol} @ {entry_price}, SL={sl:.2f}, TP={tp:.2f}")

    def close_position(self, symbol: str, exit_price: float) -> float | None:
        """Close a position and return PnL.

        Updates daily PnL tracking and consecutive win/loss counters.

        Args:
            symbol: Trading pair symbol.
            exit_price: Exit price.

        Returns:
            Realized PnL in quote currency, or None if no position exists.
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        # Track daily PnL
        today = datetime.now(_TZ).date()
        self.daily_pnl[today] = self.daily_pnl.get(today, 0) + pnl

        # Update daily max drawdown
        current_equity = self.total_capital + self.daily_pnl.get(today, 0)
        if current_equity > self.daily_peak_equity:
            self.daily_peak_equity = current_equity
        drawdown = self.daily_peak_equity - current_equity
        if drawdown > self.daily_max_drawdown:
            self.daily_max_drawdown = drawdown

        # Track consecutive losses (reset only if profit exceeds 0.1% of total capital)
        if pnl < 0:
            self.consecutive_losses += 1
        elif pnl > self.total_capital * 0.001:
            self.consecutive_losses = 0

        del self.positions[symbol]
        logger.info(f"Closed {symbol} @ {exit_price}, PnL={pnl:.2f}")
        return pnl

    def check_stop_conditions(self, symbol: str, current_price: float) -> bool:
        """Check if stop loss or take profit is triggered.

        Args:
            symbol: Trading pair symbol.
            current_price: Current market price.

        Returns:
            True if stop-loss or take-profit is triggered, False otherwise.
        """
        if symbol not in self.positions:
            return False

        pos = self.positions[symbol]
        if pos.side == "LONG":
            if current_price <= pos.stop_loss:
                logger.warning(f"Stop loss triggered for {symbol} @ {current_price}")
                return True
            if current_price >= pos.take_profit:
                logger.info(f"Take profit triggered for {symbol} @ {current_price}")
                return True
        else:
            if current_price >= pos.stop_loss:
                logger.warning(f"Stop loss triggered for {symbol} @ {current_price}")
                return True
            if current_price <= pos.take_profit:
                logger.info(f"Take profit triggered for {symbol} @ {current_price}")
                return True

        return False

    def get_risk_summary(self) -> dict[str, Any]:
        """Get current risk status summary.

        Returns:
            Dictionary with keys: trading_paused, pause_reason,
            total_capital, open_positions, total_exposure_pct,
            daily_pnl, consecutive_losses, positions (list of dicts).
        """
        today = datetime.now(_TZ).date()
        daily_pnl = sum(pnl for d, pnl in self.daily_pnl.items() if d == today)
        total_value = sum(
            p.quantity * p.entry_price / p.leverage
            for p in self.positions.values()
        )

        return {
            'trading_paused': self.trading_paused,
            'pause_reason': self.pause_reason,
            'total_capital': round(self.total_capital, 2),
            'open_positions': len(self.positions),
            'total_exposure_pct': round(total_value / self.total_capital * 100, 2),
            'daily_pnl': round(daily_pnl, 2),
            'consecutive_losses': self.consecutive_losses,
            'positions': [
                {
                    'symbol': p.symbol,
                    'side': p.side,
                    'entry_price': p.entry_price,
                    'quantity': p.quantity,
                    'leverage': p.leverage,
                    'stop_loss': p.stop_loss,
                    'take_profit': p.take_profit,
                }
                for p in self.positions.values()
            ]
        }

    def pause_trading(self, reason: str) -> None:
        """Pause trading with a reason.

        Args:
            reason: Human-readable reason for pausing.
        """
        self.trading_paused = True
        self.pause_reason = reason
        self.pause_until = datetime.now(_TZ) + timedelta(hours=1)
        logger.warning(f"Trading paused: {reason}")
        # 发送熔断通知
        try:
            from execution.notifier import get_notifier
            get_notifier().risk_alert(
                "交易熔断",
                f"原因: {reason}\n暂停至: {self.pause_until.strftime('%H:%M:%S')}\n1小时后自动恢复"
            )
        except Exception:
            pass

    def resume_trading(self) -> None:
        """Resume trading after a pause."""
        self.trading_paused = False
        self.pause_reason = ""
        self.pause_until = None
        logger.info("Trading resumed")

    def _reset_daily_stats(self) -> None:
        """Reset daily drawdown tracking at midnight."""
        self.daily_max_drawdown = 0.0
        self.daily_peak_equity = 0.0
