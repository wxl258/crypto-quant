"""
Risk Manager - Position sizing and risk control
"""
import numpy as np
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, date
import logging

from config import get_timezone

logger = logging.getLogger(__name__)

# 安全获取时区 — Android/Chaquopy 可能缺少 zoneinfo 数据
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(get_timezone())
except Exception:
    logger.warning(f"ZoneInfo not available, falling back to UTC")
    try:
        from datetime import timezone, timedelta
        _TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
    except Exception:
        _TZ = None


@dataclass
class RiskLimits:
    """Risk control limits"""
    max_position_pct: float = 0.3
    max_total_position_pct: float = 0.8
    max_daily_loss_pct: float = 0.05
    max_consecutive_losses: int = 3
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    position_sizing: str = "fixed"  # fixed, kelly, atr


@dataclass
class PositionInfo:
    """Current position information"""
    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float = 0.0
    take_profit: float = 0.0


class RiskManager:
    """Manages trading risk including position sizing and risk checks"""
    
    def __init__(self, limits=None, initial_capital: float = 10000.0):
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
        self.positions: Dict[str, PositionInfo] = {}
        self.daily_pnl: Dict[date, float] = {}
        self.consecutive_losses = 0
        self.total_capital = initial_capital
        self.trading_paused = False
        self.pause_reason = ""
        # 最大回撤熔断
        self.peak_equity = initial_capital
        self.max_drawdown_fuse_pct = 0.20
        self._trading_paused = False
        self._pause_reason = ""
    
    def set_capital(self, capital: float):
        """Update total capital"""
        self.total_capital = capital
    
    def calculate_position_size(self, symbol: str, price: float,
                                 leverage: int = 3,
                                 atr: float = None) -> float:
        """
        Calculate position size based on risk parameters.
        
        Returns quantity in base asset units.
        """
        if self.limits.position_sizing == "fixed":
            # Fixed percentage of capital
            position_value = self.total_capital * self.limits.max_position_pct
            quantity = position_value * leverage / price
            
        elif self.limits.position_sizing == "kelly":
            # Kelly Criterion (simplified)
            # W = win_rate, R = avg_win/avg_loss ratio
            # Assume 50% win rate with 1.5:1 reward/risk
            win_rate = 0.5
            reward_risk = 1.5
            kelly_pct = win_rate - (1 - win_rate) / reward_risk
            kelly_pct = max(0.05, min(kelly_pct, 0.25))  # Cap at 5%-25%
            position_value = self.total_capital * kelly_pct
            quantity = position_value * leverage / price
            
        elif self.limits.position_sizing == "atr" and atr is not None:
            # Risk based on ATR - risk 1% of capital per trade
            risk_amount = self.total_capital * 0.01
            stop_distance = atr * 2  # 2 ATR stop
            quantity = risk_amount * leverage / stop_distance
        else:
            position_value = self.total_capital * self.limits.max_position_pct
            quantity = position_value * leverage / price
        
        return max(0.001, quantity)  # Minimum quantity
    
    def calculate_stop_loss(self, entry_price: float, side: str,
                           atr: float = None) -> float:
        """Calculate stop loss price"""
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
        """Calculate take profit price"""
        tp_pct = self.limits.take_profit_pct
        if side == "LONG":
            return entry_price * (1 + tp_pct)
        else:
            return entry_price * (1 - tp_pct)
    
    def can_open_position(self, symbol: str, side: str) -> tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Returns (allowed, reason)
        """
        if self.trading_paused:
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
        
        # Check daily loss limit (only net losses trigger the limit)
        today = datetime.now(_TZ).date()
        daily_pnl_total = sum(
            pnl for d, pnl in self.daily_pnl.items() if d == today
        )
        if daily_pnl_total < 0 and abs(daily_pnl_total) >= self.total_capital * self.limits.max_daily_loss_pct:
            return False, "日亏损已达上限"
        
        return True, "OK"
    
    def open_position(self, symbol: str, side: str, entry_price: float,
                      quantity: float, leverage: int = 3,
                      atr: float = None):
        """Record a new position"""
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
    
    def close_position(self, symbol: str, exit_price: float) -> Optional[float]:
        """Close a position and return PnL"""
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
        
        # Track consecutive losses
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        
        del self.positions[symbol]
        logger.info(f"Closed {symbol} @ {exit_price}, PnL={pnl:.2f}")
        return pnl
    
    def check_stop_conditions(self, symbol: str, current_price: float) -> bool:
        """Check if stop loss or take profit is triggered"""
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
    
    def get_risk_summary(self) -> Dict:
        """Get current risk status summary"""
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
    
    def check_drawdown_fuse(self, current_equity: float) -> tuple:
        """Check if max drawdown fuse is triggered. Returns (triggered, reason)."""
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        dd = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0
        if dd > self.max_drawdown_fuse_pct:
            self._trading_paused = True
            self._pause_reason = f"最大回撤熔断: {dd:.1%}"
            return (True, self._pause_reason)
        return (False, "")
    
    def check_portfolio_risk(self, positions: dict, current_prices: dict) -> tuple:
        """组合层面风控检查"""
        if not positions:
            return (True, "")
        
        # 计算组合总敞口
        total_exposure = 0.0
        for sym, pos in positions.items():
            price = current_prices.get(sym, pos.get('entry_price', 0))
            qty = pos.get('quantity', 0)
            leverage = pos.get('leverage', 1)
            total_exposure += qty * price * leverage
        
        exposure_pct = total_exposure / self.peak_equity if self.peak_equity > 0 else 1.0
        
        if exposure_pct > self.limits.max_total_position_pct:
            self._trading_paused = True
            self._pause_reason = f"组合敞口超限: {exposure_pct:.1%} > {self.limits.max_total_position_pct:.1%}"
            return (False, self._pause_reason)
        
        return (True, "")

    def pause_trading(self, reason: str):
        """Pause trading with reason"""
        self.trading_paused = True
        self.pause_reason = reason
        logger.warning(f"Trading paused: {reason}")
    
    def resume_trading(self):
        """Resume trading"""
        self.trading_paused = False
        self.pause_reason = ""
        logger.info("Trading resumed")
