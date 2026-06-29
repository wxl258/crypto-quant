"""
Turtle Trading Strategy v2 — Dual-channel + Volume Filter + ATR Trailing Stop
==============================================================================
Core improvements over v1:
1. **Dual-channel entry**: Primary entry on 20-period channel break.  Also allows
   entry on 10-period channel break when the 20-period channel was already broken
   within the last N bars (trend continuation).  This catches secondary entries
   in strong trends that the original Turtle missed.
2. **Volume confirmation**: Entry only fires when current bar volume exceeds the
   20-period average.  Filters out low-conviction breakouts.
3. **ATR trailing stop**: For long positions, tracks the highest high since entry
   and exits when price drops below (highest_high - ATR * stop_multiplier).
   For shorts, tracks the lowest low since entry and exits when price rises above
   (lowest_low + ATR * stop_multiplier).  This is the classic Turtle exit
   enhancement.
4. **Exit signals**: Opposite 10-period channel break OR ATR trailing stop hit.
"""
from typing import Dict, List
import numpy as np
import pandas as pd
from .base import Strategy, Signal, SignalType

# --- Module-level constants ---
_TAKE_PROFIT_ATR_MULTIPLIER = 3
_VOLUME_MA_WINDOW = 20


class TurtleStrategy(Strategy):
    """Turtle Trading — Dual Donchian channel breakout with volume filter and
    ATR trailing stop.

    Entry:
        Long:  Price > 20-bar high AND volume > 20-bar avg volume
               OR Price > 10-bar high (trend continuation, if 20-bar
               channel already broken within recent bars)
        Short: Price < 20-bar low AND volume > 20-bar avg volume
               OR Price < 10-bar low (trend continuation)
    Exit:
        - Opposite 10-bar channel break
        - ATR trailing stop hit
    """

    def _default_params(self):
        return {
            'entry_period': 20,
            'exit_period': 10,
            'atr_period': 20,
            'atr_stop': 2.0,
            'use_volume_filter': True,
            'continuation_lookback': 20,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "entry_period", "type": "int", "default": 20, "min": 10, "max": 100, "label": "入场通道周期"},
            {"name": "exit_period", "type": "int", "default": 10, "min": 5, "max": 50, "label": "出场通道周期"},
            {"name": "atr_period", "type": "int", "default": 20, "min": 10, "max": 50, "label": "ATR周期"},
            {"name": "atr_stop", "type": "float", "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.5, "label": "ATR止损倍数"},
            {"name": "use_volume_filter", "type": "bool", "default": True, "label": "成交量过滤"},
            {"name": "continuation_lookback", "type": "int", "default": 20, "min": 5, "max": 50, "label": "趋势延续回溯"},
        ]

    def init(self):
        high = self.data['high'].values
        low = self.data['low'].values
        close = self.data['close'].values

        entry_p = self.get_param('entry_period', 20)
        exit_p = self.get_param('exit_period', 10)
        atr_p = self.get_param('atr_period', 20)

        # Donchian channels — vectorized rolling max/min
        entry_high = pd.Series(high).rolling(window=entry_p, min_periods=entry_p).max().values
        entry_low = pd.Series(low).rolling(window=entry_p, min_periods=entry_p).min().values
        exit_high = pd.Series(high).rolling(window=exit_p, min_periods=exit_p).max().values
        exit_low = pd.Series(low).rolling(window=exit_p, min_periods=exit_p).min().values

        # ATR
        atr = self.atr(high, low, close, atr_p)

        # Volume average (for volume filter)
        if self.get_param('use_volume_filter', True) and 'volume' in self.data.columns:
            vol_avg = pd.Series(self.data['volume'].values).rolling(window=_VOLUME_MA_WINDOW, min_periods=_VOLUME_MA_WINDOW).mean().values
        else:
            vol_avg = np.full(len(close), np.nan)

        self.add_indicator('entry_high', entry_high)
        self.add_indicator('entry_low', entry_low)
        self.add_indicator('exit_high', exit_high)
        self.add_indicator('exit_low', exit_low)
        self.add_indicator('atr', atr)
        self.add_indicator('vol_avg', vol_avg)

        # State tracking
        self._entry_bar = -1
        self._highest_since_entry = 0.0
        self._lowest_since_entry = float('inf')
        self._twenty_break_bar = -1  # Last bar where 20-period channel was broken

    def _volume_confirms(self, i: int) -> bool:
        """Check if current volume exceeds 20-period average."""
        if not self.get_param('use_volume_filter', True):
            return True
        if 'volume' not in self.data.columns:
            return True
        vol_avg = self._indicators.get('vol_avg')
        if vol_avg is None or np.isnan(vol_avg[i]):
            return True
        return self.data['volume'].iloc[i] > vol_avg[i]

    def _continuation_entry_allowed(self, i: int) -> bool:
        """Check if 20-period channel was broken recently, allowing 10-period entry."""
        lookback = self.get_param('continuation_lookback', 20)
        if self._twenty_break_bar < 0:
            return False
        return (i - self._twenty_break_bar) <= lookback

    def _track_twenty_break(self, i: int, price: float) -> None:
        """Record if the 20-period channel was broken at this bar."""
        entry_high = self._indicators['entry_high']
        entry_low = self._indicators['entry_low']
        if i < 1 or np.isnan(entry_high[i-1]) or np.isnan(entry_low[i-1]):
            return
        if price > entry_high[i-1] or price < entry_low[i-1]:
            self._twenty_break_bar = i

    def next(self, i: int) -> Signal:
        entry_high = self._indicators['entry_high']
        entry_low = self._indicators['entry_low']
        exit_high = self._indicators['exit_high']
        exit_low = self._indicators['exit_low']
        atr = self._indicators['atr']
        price = self.data['close'].iloc[i]
        high_i = self.data['high'].iloc[i]
        low_i = self.data['low'].iloc[i]

        if np.isnan(entry_high[i]) or np.isnan(atr[i]) or i < 1:
            return Signal(SignalType.HOLD, "", price)

        atr_stop = self.get_param('atr_stop', 2.0)
        pos = self.get_position()

        # Track 20-period channel breaks for continuation entries
        self._track_twenty_break(i, price)

        if pos == 0:
            # --- Primary entry: 20-period channel breakout ---
            prev_entry_high = entry_high[i-1] if not np.isnan(entry_high[i-1]) else entry_high[i]
            prev_entry_low = entry_low[i-1] if not np.isnan(entry_low[i-1]) else entry_low[i]

            # Long entry
            long_primary = (price > prev_entry_high and
                          self.data['high'].iloc[i-1] <= prev_entry_high)
            # Short entry
            short_primary = (price < prev_entry_low and
                           self.data['low'].iloc[i-1] >= prev_entry_low)

            # --- Secondary entry: 10-period channel (trend continuation) ---
            prev_exit_high = exit_high[i-1] if not np.isnan(exit_high[i-1]) else exit_high[i]
            prev_exit_low = exit_low[i-1] if not np.isnan(exit_low[i-1]) else exit_low[i]

            long_continuation = (price > prev_exit_high and
                               self.data['high'].iloc[i-1] <= prev_exit_high and
                               self._continuation_entry_allowed(i))
            short_continuation = (price < prev_exit_low and
                                self.data['low'].iloc[i-1] >= prev_exit_low and
                                self._continuation_entry_allowed(i))

            # Enter long
            if (long_primary or long_continuation) and self._volume_confirms(i):
                self._entry_bar = i
                self._highest_since_entry = high_i
                self._lowest_since_entry = low_i
                sl = price - atr[i] * atr_stop
                tp = price + atr[i] * atr_stop * _TAKE_PROFIT_ATR_MULTIPLIER
                entry_type = "主突破" if long_primary else "趋势延续"
                return Signal(SignalType.BUY, "", price, stop_loss=sl, take_profit=tp,
                            reason=f"海龟{entry_type}入场(上轨={entry_high[i]:.1f})")

            # Enter short
            if (short_primary or short_continuation) and self._volume_confirms(i):
                self._entry_bar = i
                self._highest_since_entry = high_i
                self._lowest_since_entry = low_i
                sl = price + atr[i] * atr_stop
                tp = price - atr[i] * atr_stop * _TAKE_PROFIT_ATR_MULTIPLIER
                entry_type = "主突破" if short_primary else "趋势延续"
                return Signal(SignalType.SELL, "", price, stop_loss=sl, take_profit=tp,
                            reason=f"海龟{entry_type}入场(下轨={entry_low[i]:.1f})")

        elif pos == 1:
            # Update trailing stop levels
            self._highest_since_entry = max(self._highest_since_entry, high_i)
            trail_stop = self._highest_since_entry - atr[i] * atr_stop

            # Exit long: break below exit_low OR ATR trailing stop hit
            prev_exit_low = exit_low[i-1] if not np.isnan(exit_low[i-1]) else exit_low[i]
            if price < prev_exit_low:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_LONG, "", price,
                            reason=f"海龟出场(跌破{exit_low[i]:.1f})")

            if low_i <= trail_stop:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_LONG, "", price,
                            reason=f"ATR追踪止损(最高={self._highest_since_entry:.1f},止损={trail_stop:.1f})")

        elif pos == -1:
            # Update trailing stop levels
            self._lowest_since_entry = min(self._lowest_since_entry, low_i)
            trail_stop = self._lowest_since_entry + atr[i] * atr_stop

            # Exit short: break above exit_high OR ATR trailing stop hit
            prev_exit_high = exit_high[i-1] if not np.isnan(exit_high[i-1]) else exit_high[i]
            if price > prev_exit_high:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_SHORT, "", price,
                            reason=f"海龟出场(突破{exit_high[i]:.1f})")

            if high_i >= trail_stop:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_SHORT, "", price,
                            reason=f"ATR追踪止损(最低={self._lowest_since_entry:.1f},止损={trail_stop:.1f})")

        return Signal(SignalType.HOLD, "", price)
