"""
Triple-Confirmation Moving Average Strategy
三重确认均线策略

Algorithm:
- 3 MAs: fast(5), medium(20), slow(50). Entry requires ALL THREE to align:
  - LONG:  fast > medium > slow  (bullish alignment)
  - SHORT: fast < medium < slow  (bearish alignment)
- The crossover itself (fast crossing medium) is the trigger, but
  alignment must be pre-confirmed.
- Exit: fast crosses back through medium (loses alignment) OR price
  crosses slow MA (trend broken).
- Volume surge filter: only enter when volume > volume_mult * recent
  average (confirms breakout, not noise).
- Minimum hold of min_hold_bars to prevent instant whipsaw exits.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType


class DualMAStrategy(Strategy):
    """Triple-confirmation MA crossover with volume filter and minimum hold."""

    def _default_params(self):
        return {
            'fast_period': 5,
            'medium_period': 20,
            'slow_period': 50,
            'use_volume_filter': True,
            'volume_mult': 1.2,
            'min_hold_bars': 5,
            'use_ema': True,
            'atr_stop_mult': 3.0,  # ATR stop multiplier (0 = disabled)
            'atr_period': 14,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "fast_period", "type": "int", "default": 5, "min": 2, "max": 50, "label": "快线周期"},
            {"name": "medium_period", "type": "int", "default": 20, "min": 5, "max": 100, "label": "中线周期"},
            {"name": "slow_period", "type": "int", "default": 50, "min": 20, "max": 200, "label": "慢线周期"},
            {"name": "use_volume_filter", "type": "bool", "default": True, "label": "启用成交量过滤"},
            {"name": "volume_mult", "type": "float", "default": 1.2, "min": 1.0, "max": 3.0, "step": 0.1, "label": "成交量倍数"},
            {"name": "min_hold_bars", "type": "int", "default": 5, "min": 1, "max": 30, "label": "最少持仓K线数"},
            {"name": "use_ema", "type": "bool", "default": True, "label": "使用EMA"},
        ]

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values
        fast_p = self.get_param('fast_period', 5)
        medium_p = self.get_param('medium_period', 20)
        slow_p = self.get_param('slow_period', 50)
        use_ema = self.get_param('use_ema', True)

        ma_func = self.ema if use_ema else self.sma

        self.add_indicator('fast_ma', ma_func(close, fast_p))
        self.add_indicator('medium_ma', ma_func(close, medium_p))
        self.add_indicator('slow_ma', ma_func(close, slow_p))

        # ATR for stop loss
        atr_period = self.get_param('atr_period', 14)
        self.add_indicator('atr', self.atr(high, low, close, atr_period))

        # Volume average for surge detection
        volume = self.data['volume'].values
        vol_period = slow_p  # same window as slow MA for volume baseline
        self.add_indicator('vol_avg', self.sma(volume, vol_period))

        # Tracking state
        self._entry_bar = -1  # bar index when position was entered
        self._entry_price = 0.0

    def _valid(self, *arrs):
        """Check all arrays have valid (non-NaN) values at current index."""
        return all(not np.isnan(a) for a in arrs)

    def _alignment(self, fast, medium, slow):
        """Return 1 for bullish alignment, -1 for bearish, 0 for none."""
        if fast > medium > slow:
            return 1
        if fast < medium < slow:
            return -1
        return 0

    def _volume_surge(self, i: int) -> bool:
        """Check if current volume exceeds threshold * recent average."""
        use_filter = self.get_param('use_volume_filter', True)
        if not use_filter:
            return True
        vol = self.data['volume'].iloc[i]
        vol_avg = self._indicators['vol_avg'][i]
        if np.isnan(vol_avg) or vol_avg <= 0:
            return True
        vol_mult = self.get_param('volume_mult', 1.2)
        return vol > vol_mult * vol_avg

    def next(self, i: int) -> Signal:
        fast = self._indicators['fast_ma']
        medium = self._indicators['medium_ma']
        slow = self._indicators['slow_ma']
        atr = self._indicators.get('atr')
        price = self.data['close'].iloc[i]
        pos = self.get_position()
        min_hold = self.get_param('min_hold_bars', 5)
        atr_stop_mult = self.get_param('atr_stop_mult', 3.0)

        # Need valid indicators for current AND previous bar
        if i < 1:
            return Signal(SignalType.HOLD, "", price)
        if not self._valid(fast[i], medium[i], slow[i], fast[i-1], medium[i-1], slow[i-1]):
            return Signal(SignalType.HOLD, "", price)

        bars_held = i - self._entry_bar if self._entry_bar >= 0 else 999

        # ── ATR stop loss check ──
        if pos != 0 and atr_stop_mult > 0 and atr is not None and not np.isnan(atr[i]):
            stop_dist = atr_stop_mult * atr[i]
            if pos == 1 and price <= self._entry_price - stop_dist:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_LONG, "", price,
                            reason=f"ATR止损触发 @ {price:.2f} (止损={self._entry_price - stop_dist:.2f})")
            if pos == -1 and price >= self._entry_price + stop_dist:
                self._entry_bar = -1
                return Signal(SignalType.CLOSE_SHORT, "", price,
                            reason=f"ATR止损触发 @ {price:.2f} (止损={self._entry_price + stop_dist:.2f})")

        # ── EXIT logic for open positions ──
        if pos == 1:
            # Exit LONG: fast crosses back below medium (loses alignment)
            # OR price breaks below slow MA (trend broken)
            fast_cross_down = fast[i-1] >= medium[i-1] and fast[i] < medium[i]
            trend_broken = price < slow[i]
            if (fast_cross_down or trend_broken) and bars_held >= min_hold:
                self._entry_bar = -1
                reason = "快线下穿中线" if fast_cross_down else "价格跌破慢线"
                return Signal(SignalType.CLOSE_LONG, "", price, reason=f"退出多头: {reason}")
            return Signal(SignalType.HOLD, "", price)

        if pos == -1:
            # Exit SHORT: fast crosses back above medium (loses alignment)
            # OR price breaks above slow MA (trend broken)
            fast_cross_up = fast[i-1] <= medium[i-1] and fast[i] > medium[i]
            trend_broken = price > slow[i]
            if (fast_cross_up or trend_broken) and bars_held >= min_hold:
                self._entry_bar = -1
                reason = "快线上穿中线" if fast_cross_up else "价格突破慢线"
                return Signal(SignalType.CLOSE_SHORT, "", price, reason=f"退出空头: {reason}")
            return Signal(SignalType.HOLD, "", price)

        # ── ENTRY logic (pos == 0) ──
        # Detect fast/medium crossover
        golden_cross = fast[i-1] <= medium[i-1] and fast[i] > medium[i]
        death_cross = fast[i-1] >= medium[i-1] and fast[i] < medium[i]

        if not golden_cross and not death_cross:
            return Signal(SignalType.HOLD, "", price)

        # Relaxed entry: alignment can be pre-existing (not required same-bar)
        # Triple-confirmation: alignment check + crossover
        align = self._alignment(fast[i], medium[i], slow[i])

        if golden_cross and align == 1:
            if not self._volume_surge(i):
                return Signal(SignalType.HOLD, "", price)
            self._entry_bar = i
            self._entry_price = price
            return Signal(SignalType.BUY, "", price,
                          reason=f"三重确认做多: fast({fast[i]:.2f})>med({medium[i]:.2f})>slow({slow[i]:.2f})",
                          stop_loss=price - atr_stop_mult * atr[i] if atr_stop_mult > 0 and atr is not None and not np.isnan(atr[i]) else 0.0)

        if death_cross and align == -1:
            if not self._volume_surge(i):
                return Signal(SignalType.HOLD, "", price)
            self._entry_bar = i
            self._entry_price = price
            return Signal(SignalType.SELL, "", price,
                          reason=f"三重确认做空: fast({fast[i]:.2f})<med({medium[i]:.2f})<slow({slow[i]:.2f})",
                          stop_loss=price + atr_stop_mult * atr[i] if atr_stop_mult > 0 and atr is not None and not np.isnan(atr[i]) else 0.0)

        return Signal(SignalType.HOLD, "", price)
