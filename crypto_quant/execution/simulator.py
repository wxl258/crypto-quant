"""
Paper Trading Simulator - Simulated trading for testing strategies
"""
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
import logging
import uuid

from risk.manager import RiskManager, RiskLimits
from config import get_backtest_config, get_db_path
from data.store import DataStore

logger = logging.getLogger(__name__)


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
                    self.close_position(sym, sym_price, "止损/止盈触发", timestamp)
        
        # Update equity tracking
        if timestamp:
            equity = self.get_total_equity()
            self.equity_history.append({
                'timestamp': timestamp,
                'equity': equity,
            })
    
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
        
        quantity = self.risk_manager.calculate_position_size(
            symbol, price, leverage, atr
        )
        
        # Calculate cost
        position_value = quantity * price / leverage
        fee = position_value * get_backtest_config().get('commission', 0.0004)
        
        if position_value + fee > self.capital:
            logger.warning(f"Insufficient capital for {symbol}")
            return None
        
        self.capital -= fee
        
        self.risk_manager.open_position(symbol, side, price, quantity, leverage, atr)
        
        pos_id = str(uuid.uuid4())[:8]
        order = {
            'id': pos_id,
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'price': price,
            'quantity': quantity,
            'leverage': leverage,
            'fee': fee,
            'status': 'FILLED',
            'timestamp': timestamp or datetime.now(),
        }
        self.orders.append(order)
        self.order_history.append(order)
        
        self.positions[symbol] = {
            'symbol': symbol,
            'side': side,
            'entry_price': price,
            'quantity': quantity,
            'leverage': leverage,
            'open_time': timestamp or datetime.now(),
        }
        
        # Persist to database
        self._store.save_trade({
            'id': pos_id,
            'symbol': symbol,
            'side': side,
            'entry_price': price,
            'quantity': quantity,
            'leverage': leverage,
            'fee': fee,
            'status': 'OPEN',
            'open_time': str(timestamp or datetime.now()),
        })
        
        logger.info(f"PAPER: Opened {side} {symbol} x{quantity} @ {price}")
        return order
    
    def close_position(self, symbol: str, price: float, 
                       reason: str = "", timestamp: datetime = None) -> Optional[Dict]:
        """Close a simulated position"""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        pnl = self.risk_manager.close_position(symbol, price)
        
        if pnl is not None:
            self.capital += pnl
        
        order = {
            'id': str(uuid.uuid4())[:8],
            'symbol': symbol,
            'side': 'CLOSE',
            'type': 'MARKET',
            'price': price,
            'quantity': pos['quantity'],
            'entry_price': pos['entry_price'],
            'pnl': round(pnl, 2) if pnl else 0,
            'reason': reason,
            'status': 'FILLED',
            'timestamp': timestamp or datetime.now(),
        }
        self.order_history.append(order)
        
        # Update original trade in DB
        # Find the open trade for this symbol
        open_trades = self._store.load_open_positions()
        for t in open_trades:
            if t['symbol'] == symbol and t['status'] == 'OPEN':
                self._store.close_trade_in_db(
                    t['id'], price,
                    round(pnl, 2) if pnl else 0,
                    str(timestamp or datetime.now()),
                    reason,
                )
                break
        
        del self.positions[symbol]
        if pnl is not None:
            logger.info(f"PAPER: Closed {symbol} @ {price}, PnL={pnl:.2f}")
        else:
            logger.info(f"PAPER: Closed {symbol} @ {price}, PnL={pnl}")
        return order
    
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
        """Restore open positions from database on startup."""
        try:
            open_trades = self._store.load_open_positions()
            for t in open_trades:
                symbol = t['symbol']
                self.positions[symbol] = {
                    'symbol': symbol,
                    'side': t['side'],
                    'entry_price': t['entry_price'],
                    'quantity': t['quantity'],
                    'leverage': t.get('leverage', 3),
                    'open_time': t.get('open_time', ''),
                }
                # Deduct only fee (matching open_position behavior)
                position_value = t['quantity'] * t['entry_price'] / t.get('leverage', 3)
                fee = position_value * get_backtest_config().get('commission', 0.0004)
                self.capital -= fee
                self.risk_manager.open_position(
                    symbol, t['side'], t['entry_price'],
                    t['quantity'], t.get('leverage', 3),
                )
                logger.info(f"Restored position: {t['side']} {symbol} x{t['quantity']} @ {t['entry_price']}")
            
            # Also restore closed trade history
            history = self._store.load_trade_history(limit=200)
            for t in history:
                if t.get('status') == 'CLOSED':
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
                        'timestamp': t.get('close_time', ''),
                    })
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
