"""
Backtest Engine - Vectorized backtesting for trading strategies
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Type
from datetime import datetime

from strategy.base import Strategy, SignalType
from backtest.metrics import calculate_metrics


class BacktestEngine:
    """Vectorized backtest engine for evaluating trading strategies"""

    def __init__(self, initial_capital: float = 10000,
                 commission: float = 0.0004,
                 slippage: float = 0.0001,
                 position_pct: float = 0.3,
                 default_leverage: int = 3,
                 funding_rate: float = 0.0001,
                 slippage_model: str = "fixed",
                 position_sizing: str = "fixed"):
        """
        Args:
            initial_capital: Starting capital in USDT
            commission: Trading fee rate (e.g., 0.0004 for 0.04%)
            slippage: Slippage rate per trade
            position_pct: Fraction of capital to use per position (0-1)
            default_leverage: Default leverage if strategy doesn't specify
            funding_rate: Funding rate per 8h period (default 0.0001 = 0.01%)
            slippage_model: "fixed" or "volume" (volume-based slippage)
            position_sizing: Position sizing method:
                - "fixed": Always use position_pct (default, current behavior)
                - "kelly": Use Kelly Criterion based on last 20 trades.
                  f = win_rate - (1-win_rate)/(avg_win/avg_loss), capped at 0.25.
                - "anti_martingale": After a win, increase size by 20%;
                  after a loss, reset to base position_pct.
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.position_pct = position_pct
        self.default_leverage = default_leverage
        self.funding_rate = funding_rate
        self.slippage_model = slippage_model
        self.position_sizing = position_sizing

    def _detect_market_state(self, data: pd.DataFrame, i: int) -> str:
        """Detect market state at bar i for trade tagging.
        Returns: BULL / BEAR / RANGE / HIGH_VOL"""
        if i < 50:
            return 'UNKNOWN'
        close = data['close'].values
        high = data['high'].values
        low = data['low'].values
        price = close[i]
        
        # SMA slope for trend — ensure at least 20 bars in each window
        sma20 = np.mean(close[i-19:i+1])
        sma20_prev = np.mean(close[i-39:i-19]) if i >= 39 else np.mean(close[max(0,i-39):i-19])
        
        # ATR for volatility — ensure at least 14 bars
        window_high = high[max(0,i-13):i+1]
        window_low = low[max(0,i-13):i+1]
        window_close = close[max(0,i-13):i+1]
        tr = np.maximum(window_high - window_low,
                       np.maximum(np.abs(window_high - np.roll(window_close, 1)),
                                  np.abs(window_low - np.roll(window_close, 1))))
        atr = np.mean(tr[-14:]) if len(tr) >= 14 else tr[-1]
        
        vol_ratio = atr / price if price > 0 else 0
        
        if vol_ratio > 0.03:
            return 'HIGH_VOL'
        if sma20 > sma20_prev * 1.02:
            return 'BULL'
        if sma20 < sma20_prev * 0.98:
            return 'BEAR'
        return 'RANGE'

    def _get_slippage(self, i: int, slippage_factors=None) -> float:
        """Compute slippage for the current candle based on slippage_model."""
        if self.slippage_model == "volume" and slippage_factors is not None:
            return self.slippage * slippage_factors[i]
        return self.slippage

    def _compute_allocation_factor(self, trades: list) -> float:
        """Compute the dynamic allocation factor based on position_sizing method.

        Returns a multiplier in [0, 1] to apply to position_pct.
        """
        if self.position_sizing == "fixed":
            return 1.0

        # Only consider closed trades (those with pnl field)
        closed = [t for t in trades if 'pnl' in t]

        if self.position_sizing == "kelly":
            if len(closed) < 5:
                return 1.0

            recent = closed[-20:]
            wins = [t for t in recent if t['pnl'] > 0]
            losses = [t for t in recent if t['pnl'] < 0]

            if not wins or not losses:
                return 1.0

            win_rate = len(wins) / len(recent)
            avg_win = sum(t['pnl'] for t in wins) / len(wins)
            avg_loss = abs(sum(t['pnl'] for t in losses) / len(losses))

            if avg_loss == 0:
                return 1.0

            # Kelly formula: f = win_rate - (1 - win_rate) / (avg_win / avg_loss)
            win_loss_ratio = avg_win / avg_loss
            if win_loss_ratio == 0:
                return 0.0

            kelly_f = win_rate - (1 - win_rate) / win_loss_ratio

            # Cap at 0.25, floor at 0
            kelly_f = max(0.0, min(kelly_f, 0.25))
            return kelly_f

        if self.position_sizing == "anti_martingale":
            if not closed:
                return 1.0

            last_trade = closed[-1]
            if last_trade['pnl'] > 0:
                # After a win, increase size by 20% (compound up)
                return 1.0 + 0.2
            else:
                # After a loss, reset to base
                return 1.0

        return 1.0

    def run(self, strategy: Strategy, data: pd.DataFrame,
            symbol: str = "BTCUSDT") -> Dict:
        """
        Run backtest with the given strategy on historical data.

        Args:
            strategy: Strategy instance
            data: OHLCV DataFrame
            symbol: Trading symbol

        Returns:
            Dictionary with equity curve, trades, and metrics
        """
        strategy.set_data(data)
        strategy.init()

        # Integrate RiskManager for position sizing and risk checks
        from risk.manager import RiskManager, RiskLimits
        risk_cfg = {}
        try:
            from config import get_risk_config
            risk_cfg = get_risk_config()
        except:
            pass
        risk_limits = RiskLimits(
            max_position_pct=risk_cfg.get('max_position_pct', 0.3),
            max_total_position_pct=risk_cfg.get('max_total_position_pct', 0.8),
            max_daily_loss_pct=risk_cfg.get('max_daily_loss_pct', 0.05),
            max_consecutive_losses=risk_cfg.get('max_consecutive_losses', 3),
            stop_loss_pct=risk_cfg.get('stop_loss_pct', 0.05),
            take_profit_pct=risk_cfg.get('take_profit_pct', 0.10),
        )
        risk_manager = RiskManager(limits=risk_limits, initial_capital=self.initial_capital)

        capital = self.initial_capital
        position = 0      # 0: none, 1: long, -1: short
        entry_price = 0.0
        position_size = 0.0
        equity_curve = []
        trades = []

        if len(data) == 0:
            eq_df = pd.DataFrame(columns=['timestamp', 'equity', 'capital', 'position'])
            return {
                'equity_curve': eq_df, 'trades': pd.DataFrame(),
                'metrics': calculate_metrics(pd.Series(dtype=float), pd.DataFrame(), self.initial_capital),
                'symbol': symbol, 'initial_capital': self.initial_capital,
                'final_capital': self.initial_capital,
            }

        leverage = strategy.get_param('leverage', self.default_leverage)
        base_leverage = leverage
        base_alloc_pct = self.position_pct
        _last_funding_ts = None  # Timestamp-based funding: last funding deduction time

        # Dynamic risk state
        peak_capital = self.initial_capital
        consecutive_wins = 0
        consecutive_losses = 0
        trailing_stop_long = 0.0   # dynamic trailing stop for long
        trailing_stop_short = float('inf')  # dynamic trailing stop for short
        atr_locked = 0.0  # ATR at entry (locked)

        # Precompute volume-based slippage factors
        volume_data = data['volume'].values if 'volume' in data.columns else None
        avg_volume = None
        slippage_factors = None
        if self.slippage_model == "volume" and volume_data is not None and len(volume_data) > 0:
            avg_volume = float(np.mean(volume_data))
            slippage_factors = np.ones(len(data))
            for i in range(len(data)):
                current_vol = volume_data[i]
                if current_vol > 0:
                    slippage_factors[i] = 1 + avg_volume / current_vol
                else:
                    slippage_factors[i] = 2.0

        for i in range(len(data)):
            price = data['close'].iloc[i]
            timestamp = data.index[i]
            sl = self._get_slippage(i, slippage_factors)

            signal = strategy.next(i)
            signal.symbol = symbol
            signal.timestamp = timestamp

            # Compute dynamic allocation factor + volatility adjustment
            alloc_factor = self._compute_allocation_factor(trades)
            vol_adj = strategy.get_volatility_adjusted_position_pct(i, self.position_pct)
            alloc_pct = vol_adj * alloc_factor

            if signal.signal_type == SignalType.BUY and position == 0:
                # Cooldown check + RiskManager check
                if not strategy.can_enter(i):
                    equity_curve.append({'timestamp': timestamp, 'equity': capital, 'capital': capital, 'position': position})
                    continue
                # RiskManager: check if position can be opened
                risk_manager.set_capital(capital)
                allowed, reason = risk_manager.can_open_position(symbol, "LONG")
                if not allowed:
                    equity_curve.append({'timestamp': timestamp, 'equity': capital, 'capital': capital, 'position': position})
                    continue
                # Open long
                entry_price = price * (1 + sl)
                # Use signal.quantity if strategy provided it, else default allocation
                if signal.quantity and signal.quantity > 0:
                    position_size = signal.quantity * capital / entry_price * leverage
                else:
                    position_size = capital * alloc_pct / entry_price * leverage
                fee = position_size * entry_price * self.commission
                position = 1
                capital -= fee
                trailing_stop_long = price
                trailing_stop_short = float('inf')
                # Market state tagging
                market_state = self._detect_market_state(data, i)
                strategy.set_position(position, entry_price)
                strategy.record_entry(i)
                trades.append({
                    'entry_time': timestamp, 'side': 'LONG',
                    'entry_price': entry_price, 'size': position_size,
                    'market_state': market_state,
                })

            elif signal.signal_type == SignalType.SELL and position == 0:
                if not strategy.can_enter(i):
                    equity_curve.append({'timestamp': timestamp, 'equity': capital, 'capital': capital, 'position': position})
                    continue
                risk_manager.set_capital(capital)
                allowed, reason = risk_manager.can_open_position(symbol, "SHORT")
                if not allowed:
                    equity_curve.append({'timestamp': timestamp, 'equity': capital, 'capital': capital, 'position': position})
                    continue
                # Open short
                entry_price = price * (1 - sl)
                if signal.quantity and signal.quantity > 0:
                    position_size = signal.quantity * capital / entry_price * leverage
                else:
                    position_size = capital * alloc_pct / entry_price * leverage
                fee = position_size * entry_price * self.commission
                position = -1
                capital -= fee
                trailing_stop_short = price
                trailing_stop_long = 0.0
                market_state = self._detect_market_state(data, i)
                strategy.set_position(position, entry_price)
                strategy.record_entry(i)
                trades.append({
                    'entry_time': timestamp, 'side': 'SHORT',
                    'entry_price': entry_price, 'size': position_size,
                    'market_state': market_state,
                })

            elif signal.signal_type == SignalType.CLOSE_LONG and position == 1:
                # Close long
                exit_price = price * (1 - sl)
                pnl = (exit_price - entry_price) * position_size
                fee = position_size * exit_price * self.commission
                capital += pnl - fee
                trades[-1].update({
                    'exit_time': timestamp, 'exit_price': exit_price,
                    'pnl': round(pnl - fee, 2),
                    'pnl_pct': round((exit_price / entry_price - 1) * 100 * leverage, 2),
                })
                position = 0
                position_size = 0
                strategy.set_position(position, 0)
                strategy.record_exit(i)
                risk_manager.close_position(symbol, exit_price)

            elif signal.signal_type == SignalType.CLOSE_SHORT and position == -1:
                # Close short
                exit_price = price * (1 + sl)
                pnl = (entry_price - exit_price) * position_size
                fee = position_size * exit_price * self.commission
                capital += pnl - fee
                trades[-1].update({
                    'exit_time': timestamp, 'exit_price': exit_price,
                    'pnl': round(pnl - fee, 2),
                    'pnl_pct': round((1 - exit_price / entry_price) * 100 * leverage, 2),
                })
                position = 0
                position_size = 0
                strategy.set_position(position, 0)
                strategy.record_exit(i)
                risk_manager.close_position(symbol, exit_price)

            # Check signal-level stop-loss and take-profit (engine-enforced)
            if position != 0 and signal.stop_loss and signal.stop_loss > 0:
                if position == 1 and price <= signal.stop_loss:
                    pnl = (signal.stop_loss - entry_price) * position_size
                    fee = position_size * signal.stop_loss * self.commission
                    capital += pnl - fee
                    if trades:
                        trades[-1].update({
                            'exit_time': timestamp, 'exit_price': signal.stop_loss,
                            'pnl': round(pnl - fee, 2),
                            'pnl_pct': round((signal.stop_loss / entry_price - 1) * 100 * leverage, 2),
                        })
                    position = 0
                    position_size = 0
                    strategy.set_position(0, 0)
                    strategy.record_exit(i)
                elif position == -1 and price >= signal.stop_loss:
                    pnl = (entry_price - signal.stop_loss) * position_size
                    fee = position_size * signal.stop_loss * self.commission
                    capital += pnl - fee
                    if trades:
                        trades[-1].update({
                            'exit_time': timestamp, 'exit_price': signal.stop_loss,
                            'pnl': round(pnl - fee, 2),
                            'pnl_pct': round((1 - signal.stop_loss / entry_price) * 100 * leverage, 2),
                        })
                    position = 0
                    position_size = 0
                    strategy.set_position(0, 0)
                    strategy.record_exit(i)

            if position != 0 and signal.take_profit and signal.take_profit > 0:
                if position == 1 and price >= signal.take_profit:
                    pnl = (signal.take_profit - entry_price) * position_size
                    fee = position_size * signal.take_profit * self.commission
                    capital += pnl - fee
                    if trades:
                        trades[-1].update({
                            'exit_time': timestamp, 'exit_price': signal.take_profit,
                            'pnl': round(pnl - fee, 2),
                            'pnl_pct': round((signal.take_profit / entry_price - 1) * 100 * leverage, 2),
                        })
                    position = 0
                    position_size = 0
                    strategy.set_position(0, 0)
                    strategy.record_exit(i)
                elif position == -1 and price <= signal.take_profit:
                    pnl = (entry_price - signal.take_profit) * position_size
                    fee = position_size * signal.take_profit * self.commission
                    capital += pnl - fee
                    if trades:
                        trades[-1].update({
                            'exit_time': timestamp, 'exit_price': signal.take_profit,
                            'pnl': round(pnl - fee, 2),
                            'pnl_pct': round((1 - signal.take_profit / entry_price) * 100 * leverage, 2),
                        })
                    position = 0
                    position_size = 0
                    strategy.set_position(0, 0)
                    strategy.record_exit(i)

            # ============ DYNAMIC TRAILING STOP (engine-level) ============
            # Lock ATR at entry; trail stop as price moves favorably
            if position != 0:
                high_i = data['high'].iloc[i]
                low_i = data['low'].iloc[i]
                close_i = data['close'].iloc[i]

                # Compute ATR (cached in strategy if available, else compute)
                atr_arr = None
                if hasattr(strategy, '_indicators') and 'atr' in strategy._indicators:
                    atr_arr = strategy._indicators['atr']
                if atr_arr is None or np.isnan(atr_arr[i]) if atr_arr is not None else True:
                    # Fallback: compute simple ATR
                    tr = max(high_i - low_i, abs(high_i - close_i), abs(low_i - close_i))
                    atr_val = tr
                else:
                    atr_val = atr_arr[i] if not np.isnan(atr_arr[i]) else (high_i - low_i)

                if position == 1:
                    # Lock ATR on entry
                    if trailing_stop_long == 0.0 and atr_val > 0:
                        atr_locked = atr_val
                    # Trail stop: move up as price rises, lock 50% of gain
                    if price > trailing_stop_long:
                        trailing_stop_long = price
                    # Dynamic stop: only activate after price moves favorably by 1 ATR
                    effective_stop = 0.0
                    if price > entry_price + atr_locked * 1.0:
                        dyn_stop = trailing_stop_long - atr_locked * 2.5
                        hard_stop = entry_price + atr_locked * 0.5
                        effective_stop = max(dyn_stop, hard_stop)
                    if effective_stop > 0 and price <= effective_stop:
                        pnl = (effective_stop - entry_price) * position_size
                        fee = position_size * effective_stop * self.commission
                        capital += pnl - fee
                        if trades:
                            trades[-1].update({
                                'exit_time': timestamp, 'exit_price': effective_stop,
                                'pnl': round(pnl - fee, 2),
                                'pnl_pct': round((effective_stop / entry_price - 1) * 100 * leverage, 2),
                            })
                        position = 0
                        position_size = 0
                        strategy.set_position(0, 0)
                        strategy.record_exit(i)
                        trailing_stop_long = 0.0
                        atr_locked = 0.0

                elif position == -1:
                    if trailing_stop_short == float('inf') and atr_val > 0:
                        atr_locked = atr_val
                    if price < trailing_stop_short:
                        trailing_stop_short = price
                    # Dynamic stop: only activate after price moves favorably by 1 ATR
                    effective_stop = float('inf')
                    if price < entry_price - atr_locked * 1.0:
                        dyn_stop = trailing_stop_short + atr_locked * 2.5
                        hard_stop = entry_price - atr_locked * 0.5
                        effective_stop = min(dyn_stop, hard_stop)
                    if effective_stop < float('inf') and price >= effective_stop:
                        pnl = (entry_price - effective_stop) * position_size
                        fee = position_size * effective_stop * self.commission
                        capital += pnl - fee
                        if trades:
                            trades[-1].update({
                                'exit_time': timestamp, 'exit_price': effective_stop,
                                'pnl': round(pnl - fee, 2),
                                'pnl_pct': round((1 - effective_stop / entry_price) * 100 * leverage, 2),
                            })
                        position = 0
                        position_size = 0
                        strategy.set_position(0, 0)
                        strategy.record_exit(i)
                        trailing_stop_short = float('inf')
                        atr_locked = 0.0

            # ============ DYNAMIC LEVERAGE (drawdown-based) ============
            current_equity_check = capital
            if position == 1:
                current_equity_check += (price - entry_price) * position_size
            elif position == -1:
                current_equity_check += (entry_price - price) * position_size

            if current_equity_check > peak_capital:
                peak_capital = current_equity_check

            drawdown_pct = (peak_capital - current_equity_check) / peak_capital if peak_capital > 0 else 0

            # Dynamic leverage: reduce on drawdown, increase on recovery
            if drawdown_pct > 0.15:
                leverage = max(1, int(base_leverage * 0.33))   # >15% DD → 1/3 leverage
            elif drawdown_pct > 0.10:
                leverage = max(1, int(base_leverage * 0.5))    # >10% DD → 1/2 leverage
            elif drawdown_pct > 0.05:
                leverage = max(1, int(base_leverage * 0.75))   # >5% DD → 3/4 leverage
            elif consecutive_losses >= 3:
                leverage = max(1, int(base_leverage * 0.5))    # 3+ consecutive losses
            elif consecutive_wins >= 3 and drawdown_pct < 0.03:
                leverage = min(10, base_leverage + 1)          # 3+ wins + low DD → +1x
            else:
                leverage = base_leverage

            # Track consecutive wins/losses (updated when position closes)
            # This is done in the close branches above via pnl tracking
            # We use the trades list to track
            if len(trades) >= 2:
                last_closed = [t for t in trades if 'pnl' in t]
                if len(last_closed) >= 2:
                    prev = last_closed[-2]['pnl']
                    curr = last_closed[-1]['pnl']
                    if prev > 0 and curr > 0:
                        consecutive_wins = min(consecutive_wins + 1, 10)
                        consecutive_losses = 0
                    elif prev < 0 and curr < 0:
                        consecutive_losses = min(consecutive_losses + 1, 10)
                        consecutive_wins = 0
                    else:
                        consecutive_wins = 1 if curr > 0 else 0
                        consecutive_losses = 1 if curr < 0 else 0

            # Calculate current equity (including unrealized PnL)
            unrealized = 0
            if position == 1:
                unrealized = (price - entry_price) * position_size
            elif position == -1:
                unrealized = (entry_price - price) * position_size

            # Funding rate: deduct every 8 hours based on actual timestamps
            if self.funding_rate > 0 and position != 0:
                if isinstance(timestamp, (datetime, pd.Timestamp)):
                    if _last_funding_ts is None:
                        _last_funding_ts = timestamp
                    hours_elapsed = (timestamp - _last_funding_ts).total_seconds() / 3600
                    if hours_elapsed >= 8:
                        cycles = int(hours_elapsed // 8)
                        funding_cost = position_size * price * self.funding_rate * cycles
                        capital -= funding_cost
                        _last_funding_ts = timestamp
                else:
                    # Fallback for non-timestamp index: use bar count (8 bars per cycle)
                    if _last_funding_ts is None:
                        _last_funding_ts = 0
                    _last_funding_ts += 1
                    if _last_funding_ts >= 8:
                        funding_cost = position_size * price * self.funding_rate
                        capital -= funding_cost
                        _last_funding_ts = 0

            current_equity = capital + unrealized
            equity_curve.append({
                'timestamp': timestamp,
                'equity': current_equity,
                'capital': capital,
                'position': position,
            })

        # Close any open position at the end
        if position != 0:
            last_price = data['close'].iloc[-1]
            last_sl = self._get_slippage(len(data) - 1, slippage_factors)
            if position == 1:
                exit_price = last_price * (1 - last_sl)
                pnl = (exit_price - entry_price) * position_size
            else:
                exit_price = last_price * (1 + last_sl)
                pnl = (entry_price - exit_price) * position_size
            fee = position_size * exit_price * self.commission
            capital += pnl - fee
            strategy.set_position(0, 0)
            strategy.record_exit(len(data) - 1)  # track exit for cooldown

            if trades:
                trades[-1].update({
                    'exit_time': data.index[-1],
                    'exit_price': exit_price,
                    'pnl': round(pnl - fee, 2),
                    'pnl_pct': round((exit_price / entry_price - 1) * 100 * leverage, 2) if position == 1
                               else round((1 - exit_price / entry_price) * 100 * leverage, 2),
                })

        equity_df = pd.DataFrame(equity_curve)
        equity_df.set_index('timestamp', inplace=True)
        equity_series = equity_df['equity']

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        metrics = calculate_metrics(equity_series, trades_df, self.initial_capital)

        return {
            'equity_curve': equity_df,
            'trades': trades_df,
            'metrics': metrics,
            'symbol': symbol,
            'initial_capital': self.initial_capital,
            'final_capital': round(capital, 2),
        }

    def run_multiple(self, strategy_cls: Type[Strategy], params_list: List[Dict],
                     data: pd.DataFrame, symbol: str = "BTCUSDT") -> List[Dict]:
        """Run backtest with multiple parameter combinations (optimization)"""
        results = []
        for params in params_list:
            strategy = strategy_cls(params)
            result = self.run(strategy, data, symbol)
            result['params'] = params
            results.append(result)
        return results
