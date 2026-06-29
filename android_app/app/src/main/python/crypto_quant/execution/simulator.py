"""
Paper Trading Simulator - Simulated trading for testing strategies
"""
import random
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
import logging
import uuid

from risk.manager import RiskManager, RiskLimits
from config import get_backtest_config, get_db_path
from data.store import DataStore

logger = logging.getLogger(__name__)

_MAX_ORDER_HISTORY = 10000
_MAX_EQUITY_HISTORY = 5000


class PaperTradingSimulator:
    """Simulated trading environment for strategy testing"""

    def __init__(self, initial_capital: float = 10000,
                 risk_limits: RiskLimits = None):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.risk_manager = RiskManager(risk_limits)
        self.risk_manager.set_capital(initial_capital)

        self.positions: Dict[str, Dict] = {}
        self.orders: List[Dict] = []
        self.order_history: List[Dict] = []
        self.equity_history: List[Dict] = []
        self._current_price: Dict[str, float] = {}

        # Track exchange-native stop/take-profit order IDs per symbol
        self._stop_order_ids: Dict[str, str] = {}
        self._tp_order_ids: Dict[str, str] = {}

        # Persistence layer
        self._store = DataStore(get_db_path())

        # Restore open positions from DB on startup
        self._restore_state()
    
    def update_price(self, symbol: str, price: float, timestamp: datetime = None):
        """Update current market price and check stops"""
        self._current_price[symbol] = price
        
        # Check stop loss / take profit for each position using its own price
        for sym in list(self.positions.keys()):
            sym_price = self._current_price.get(sym)
            if sym_price is not None:
                if self.risk_manager.check_stop_conditions(sym, sym_price):
                    pos = self.positions.get(sym, {})
                    side = pos.get('side', 'LONG')
                    entry = pos.get('entry_price', 0)
                    self.close_position(sym, sym_price, "止损/止盈触发", timestamp)
                    # 发送止损通知
                    try:
                        from execution.notifier import get_notifier
                        pnl = (sym_price - entry) if side == 'LONG' else (entry - sym_price)
                        get_notifier().risk_alert(
                            f"{sym} 止损/止盈触发",
                            f"方向: {side}\n入场: ${entry:,.2f}\n出场: ${sym_price:,.2f}\n盈亏: ${pnl:,.2f}"
                        )
                    except Exception:
                        pass
        
        # Update equity tracking
        if timestamp:
            equity = self.get_total_equity()
            self.equity_history.append({
                'timestamp': timestamp,
                'equity': equity,
            })
            self._trim_equity_history()
    
    def get_price(self, symbol: str) -> float:
        """Get current price for symbol"""
        return self._current_price.get(symbol, 0)
    
    def open_position(self, symbol: str, side: str, price: float,
                      leverage: int = 3, atr: float = None,
                      timestamp: datetime = None) -> Optional[Dict]:
        """
        Open a simulated position.
        side: 'LONG' or 'SHORT'
        """
        allowed, reason = self.risk_manager.can_open_position(symbol, side)
        if not allowed:
            logger.warning(f"Cannot open {side} {symbol}: {reason}")
            return None

        # Apply slippage (±0.05% random)
        slippage = price * random.uniform(-0.0005, 0.0005)
        executed_price = price + slippage

        quantity = self.risk_manager.calculate_position_size(
            symbol, executed_price, leverage, atr
        )

        # Calculate cost: position_value = margin, fee is extra
        position_value = quantity * executed_price / leverage
        commission_rate = get_backtest_config().get('commission', 0.0004)
        fee = position_value * commission_rate

        # Check: margin + fee must be <= capital
        if position_value + fee > self.capital:
            logger.warning(f"Insufficient capital for {symbol}: need {position_value + fee:.2f}, have {self.capital:.2f}")
            return None

        # Deduct margin + fee from capital
        self.capital -= (position_value + fee)

        self.risk_manager.open_position(symbol, side, executed_price, quantity, leverage, atr)

        pos_id = str(uuid.uuid4())[:8]
        order = {
            'id': pos_id,
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'price': executed_price,
            'quantity': quantity,
            'leverage': leverage,
            'position_value': position_value,
            'fee': fee,
            'status': 'FILLED',
            'timestamp': timestamp or datetime.now(),
        }
        self.orders.append(order)
        self.order_history.append(order)
        self._trim_order_history()

        self.positions[symbol] = {
            'symbol': symbol,
            'side': side,
            'entry_price': executed_price,
            'quantity': quantity,
            'leverage': leverage,
            'position_value': position_value,
            'open_time': timestamp or datetime.now(),
        }

        # Submit exchange-native stop-loss and take-profit orders
        # (only effective when using a real exchange client via strategy layer;
        #  in paper mode these are tracked but the risk manager handles triggers locally.)
        pos_info = self.risk_manager.positions.get(symbol)
        if pos_info is not None:
            stop_loss = getattr(pos_info, 'stop_loss', 0.0)
            take_profit = getattr(pos_info, 'take_profit', 0.0)
            close_side = 'sell' if side.upper() == 'LONG' else 'buy'
            if stop_loss > 0:
                self._stop_order_ids[symbol] = f"sl_{symbol}_{pos_id}"
                logger.debug(
                    "SL order would be placed: %s %s qty=%s stop=%.2f",
                    symbol, close_side, quantity, stop_loss,
                )
            if take_profit > 0:
                self._tp_order_ids[symbol] = f"tp_{symbol}_{pos_id}"
                logger.debug(
                    "TP order would be placed: %s %s qty=%s tp=%.2f",
                    symbol, close_side, quantity, take_profit,
                )

        # Persist to database
        self._store.save_trade({
            'symbol': symbol,
            'side': side,
            'entry_price': executed_price,
            'quantity': quantity,
            'leverage': leverage,
            'fee': fee,
            'status': 'open',
            'entry_time': str(timestamp or datetime.now()),
            'reason': '',
        })

        logger.info(f"PAPER: Opened {side} {symbol} x{quantity} @ {executed_price:.2f} (margin={position_value:.2f}, fee={fee:.4f})")
        return order
    
    def close_position(self, symbol: str, price: float, 
                       reason: str = "", timestamp: datetime = None) -> Optional[Dict]:
        """Close a simulated position."""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]

        # Apply slippage to exit price
        slippage = price * random.uniform(-0.0005, 0.0005)
        exit_price = price + slippage

        # Calculate PnL through risk manager
        pnl = self.risk_manager.close_position(symbol, exit_price)

        # Calculate close fee
        position_value = pos.get('position_value', pos['quantity'] * pos['entry_price'] / pos['leverage'])
        commission_rate = get_backtest_config().get('commission', 0.0004)
        close_fee = position_value * commission_rate

        if pnl is not None:
            pnl -= close_fee

        # Release margin + PnL back to capital
        self.capital += position_value
        if pnl is not None:
            self.capital += pnl
        
        order = {
            'id': str(uuid.uuid4())[:8],
            'symbol': symbol,
            'side': 'CLOSE',
            'type': 'MARKET',
            'price': exit_price,
            'quantity': pos['quantity'],
            'entry_price': pos['entry_price'],
            'pnl': round(pnl, 2) if pnl else 0,
            'fee': round(close_fee, 4),
            'reason': reason,
            'status': 'FILLED',
            'timestamp': timestamp or datetime.now(),
        }
        self.order_history.append(order)
        self._trim_order_history()
        
        # Cancel associated stop-loss and take-profit orders
        self._cancel_sl_tp_orders(symbol)

        # Update in DB
        open_trades = self._store.load_open_positions()
        for t in open_trades:
            if t.get('symbol') == symbol and t.get('status') == 'open':
                self._store.close_trade_in_db(
                    t['id'], exit_price,
                    round(pnl, 2) if pnl else 0,
                    str(timestamp or datetime.now()),
                    reason,
                )
                break
        
        del self.positions[symbol]
        logger.info(f"PAPER: Closed {symbol} @ {exit_price:.2f}, PnL={pnl:.2f}" if pnl else f"PAPER: Closed {symbol} @ {exit_price:.2f}")
        return order

    # ── Memory management ─────────────────────────────────────────────────

    def _trim_order_history(self):
        """Trim order_history when it exceeds _MAX_ORDER_HISTORY entries."""
        if len(self.order_history) > _MAX_ORDER_HISTORY:
            self.order_history = self.order_history[-5000:]

    def _trim_equity_history(self):
        """Trim equity_history when it exceeds _MAX_EQUITY_HISTORY entries.

        Downsamples by keeping every 10th entry and retaining the last 1000 entries.
        """
        if len(self.equity_history) > _MAX_EQUITY_HISTORY:
            self.equity_history = self.equity_history[::10][-1000:]

    def _cancel_sl_tp_orders(self, symbol: str):
        """Cancel tracked stop-loss and take-profit orders for a symbol."""
        sl_id = self._stop_order_ids.pop(symbol, None)
        tp_id = self._tp_order_ids.pop(symbol, None)
        if sl_id:
            logger.debug("Cancelling SL order %s for %s", sl_id, symbol)
        if tp_id:
            logger.debug("Cancelling TP order %s for %s", tp_id, symbol)

    # ── Account ──────────────────────────────────────────────────────────

    def get_total_equity(self) -> float:
        """Calculate total equity including unrealized PnL"""
        unrealized = 0
        for symbol, pos in self.positions.items():
            current_price = self._current_price.get(symbol, pos['entry_price'])
            if pos['side'] == 'LONG':
                unrealized += (current_price - pos['entry_price']) * pos['quantity']
            else:
                unrealized += (pos['entry_price'] - current_price) * pos['quantity']
        return self.capital + unrealized
    
    def get_account_summary(self) -> Dict:
        """Get account summary"""
        total_equity = self.get_total_equity()
        return {
            'initial_capital': self.initial_capital,
            'capital': round(self.capital, 2),
            'total_equity': round(total_equity, 2),
            'total_pnl': round(total_equity - self.initial_capital, 2),
            'total_pnl_pct': round((total_equity / self.initial_capital - 1) * 100, 2),
            'open_positions': len(self.positions),
            'positions': [
                {
                    'symbol': p['symbol'],
                    'side': p['side'],
                    'entry_price': p['entry_price'],
                    'current_price': self._current_price.get(p['symbol'], p['entry_price']),
                    'quantity': p['quantity'],
                    'leverage': p['leverage'],
                    'unrealized_pnl': round(
                        (self._current_price.get(p['symbol'], p['entry_price']) - p['entry_price']) 
                        * p['quantity'] * (1 if p['side'] == 'LONG' else -1), 2
                    ),
                }
                for p in self.positions.values()
            ],
            'total_trades': len([o for o in self.order_history if o['side'] == 'CLOSE']),
            'risk': self.risk_manager.get_risk_summary(),
        }
    
    def get_equity_curve(self) -> pd.DataFrame:
        """Get equity curve as DataFrame"""
        if not self.equity_history:
            return pd.DataFrame(columns=['timestamp', 'equity'])
        df = pd.DataFrame(self.equity_history)
        df.set_index('timestamp', inplace=True)
        return df
    
    def _restore_state(self):
        """Restore open positions and capital from database on startup."""
        try:
            open_trades = self._store.load_open_positions()
            for t in open_trades:
                symbol = t['symbol']
                position_value = t['quantity'] * t['entry_price'] / t.get('leverage', 3)
                fee = t.get('fee', 0)

                self.positions[symbol] = {
                    'symbol': symbol,
                    'side': t['side'],
                    'entry_price': t['entry_price'],
                    'quantity': t['quantity'],
                    'leverage': t.get('leverage', 3),
                    'position_value': position_value,
                    'open_time': t.get('entry_time', ''),
                }
                # Deduct margin + fee (matching open_position)
                self.capital -= (position_value + fee)
                self.risk_manager.open_position(
                    symbol, t['side'], t['entry_price'],
                    t['quantity'], t.get('leverage', 3),
                )
                logger.info(f"Restored position: {t['side']} {symbol} x{t['quantity']} @ {t['entry_price']}")

            # Restore closed PnL from trade history
            history = self._store.load_trade_history(limit=200)
            for t in history:
                if t.get('status') == 'closed':
                    self.order_history.append({
                        'id': t['id'],
                        'symbol': t['symbol'],
                        'side': 'CLOSE',
                        'entry_price': t['entry_price'],
                        'price': t.get('exit_price', 0),
                        'quantity': t['quantity'],
                        'pnl': t.get('pnl', 0),
                        'reason': t.get('reason', ''),
                        'status': 'FILLED',
                        'timestamp': t.get('exit_time', ''),
                    })
                    # Add realized PnL to capital
                    self.capital += t.get('pnl', 0)
                    # Release margin
                    pos_val = t['quantity'] * t['entry_price'] / t.get('leverage', 3)
                    self.capital += pos_val
        except Exception as e:
            logger.warning(f"Failed to restore state from DB: {e}")
    
    def reset(self):
        """Reset simulator state"""
        self.capital = self.initial_capital
        self.positions = {}
        self.orders = []
        self.order_history = []
        self.equity_history = []
        self._current_price = {}
        self.risk_manager = RiskManager(limits=self.risk_manager.limits, initial_capital=self.initial_capital)
        self.risk_manager.set_capital(self.initial_capital)
