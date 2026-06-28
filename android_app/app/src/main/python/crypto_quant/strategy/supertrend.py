"""
Multi-Timeframe SuperTrend Strategy
多时间框架超级趋势策略

Algorithm:
- Computes TWO SuperTrends with different parameters:
  - Fast: ATR period=7, multiplier=2.0 (sensitive to short-term moves)
  - Slow: ATR period=14, multiplier=3.0 (captures broader trend)
- Entry: BOTH SuperTrends must agree on direction.
  - Both uptrend → LONG
  - Both downtrend → SHORT
- Exit: EITHER SuperTrend flips direction → close position.
  The slow ST filters out the fast ST's noise, dramatically reducing
  false signals.
- Cooldown: minimum bars between trades to avoid overtrading.
- Volatility-adaptive multiplier: scales ST multiplier based on current ATR
  relative to its 50-period average, reducing false signals in chop.

SuperTrend algorithm (per parameter set):
  hl2 = (high + low) / 2
  upper_band = hl2 + multiplier * ATR
  lower_band = hl2 - multiplier * ATR
  Band narrowing applied (standard SuperTrend logic).
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType


class SuperTrendStrategy(Strategy):
    """Multi-timeframe SuperTrend with dual confirmation and volatility-adaptive multiplier."""

    def _default_params(self):
        return {
            'fast_atr': 7,
            'fast_mult': 2.0,
            'slow_atr': 14,
            'slow_mult': 3.0,
            'cooldown_bars': 10,
            'use_adaptive_mult': True,
            'atr_avg_period': 50,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "fast_atr", "type": "int", "default": 7, "min": 3, "max": 30, "label": "快ST-ATR周期"},
            {"name": "fast_mult", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5, "label": "快ST-乘数"},
            {"name": "slow_atr", "type": "int", "default": 14, "min": 5, "max": 50, "label": "慢ST-ATR周期"},
            {"name": "slow_mult", "type": "float", "default": 3.0, "min": 1.0, "max": 5.0, "step": 0.5, "label": "慢ST-乘数"},
            {"name": "cooldown_bars", "type": "int", "default": 10, "min": 0, "max": 50, "label": "冷却K线数"},
            {"name": "use_adaptive_mult", "type": "bool", "default": True, "label": "自适应乘数(波动率调整)"},
            {"name": "atr_avg_period", "type": "int", "default": 50, "min": 20, "max": 100, "label": "ATR均线周期"},
        ]

    def _compute_supertrend(self, high, low, close, atr_period, multiplier, adaptive_factors=None):
        """Compute SuperTrend indicator with optional adaptive multiplier.

        Args:
            adaptive_factors: Optional array of per-bar multiplier adjustment factors.
                              Each value is a scalar to multiply the base multiplier.

        Returns: (trend, supertrend_line)
            trend: 1=uptrend, -1=downtrend, 0=no data
            supertrend_line: the ST line value at each bar
        """
        atr_values = self.atr(high, low, close, atr_period)
        n = len(close)
        hl2 = (high + low) / 2

        upper_band = np.full(n, np.nan, dtype=float)
        lower_band = np.full(n, np.nan, dtype=float)
        st_line = np.full(n, np.nan, dtype=float)
        trend = np.zeros(n, dtype=int)

        for i in range(atr_period, n):
            if np.isnan(atr_values[i]):
                continue

            # Apply adaptive multiplier if provided
            effective_mult = multiplier
            if adaptive_factors is not None and not np.isnan(adaptive_factors[i]):
                effective_mult = multiplier * adaptive_factors[i]

            upper_band[i] = hl2[i] + effective_mult * atr_values[i]
            lower_band[i] = hl2[i] - effective_mult * atr_values[i]

            if i == atr_period:
                trend[i] = 1 if close[i] > upper_band[i] else -1
            else:
                # Band narrowing logic
                if upper_band[i] < upper_band[i-1] or close[i-1] > upper_band[i-1]:
                    if not np.isnan(upper_band[i-1]):
                        upper_band[i] = min(upper_band[i], upper_band[i-1])
                if lower_band[i] > lower_band[i-1] or close[i-1] < lower_band[i-1]:
                    if not np.isnan(lower_band[i-1]):
                        lower_band[i] = max(lower_band[i], lower_band[i-1])

                if trend[i-1] == 1:
                    if close[i] < lower_band[i]:
                        trend[i] = -1
                    else:
                        trend[i] = 1
                        if not np.isnan(lower_band[i-1]):
                            lower_band[i] = max(lower_band[i], lower_band[i-1])
                else:
                    if close[i] > upper_band[i]:
                        trend[i] = 1
                    else:
                        trend[i] = -1
                        if not np.isnan(upper_band[i-1]):
                            upper_band[i] = min(upper_band[i], upper_band[i-1])

            st_line[i] = lower_band[i] if trend[i] == 1 else upper_band[i]

        return trend, st_line

    def init(self):
        self._last_trade_bar = -999
        high = self.data['high'].values
        low = self.data['low'].values
        close = self.data['close'].values

        fast_atr_p = self.get_param('fast_atr', 7)
        fast_mult = self.get_param('fast_mult', 2.0)
        slow_atr_p = self.get_param('slow_atr', 14)
        slow_mult = self.get_param('slow_mult', 3.0)

        use_adaptive = self.get_param('use_adaptive_mult', True)
        adaptive_factors = None

        if use_adaptive and len(close) > 50:
            atr_avg_period = self.get_param('atr_avg_period', 50)
            # Compute ATR and its SMA for volatility ratio
            atr_full = self.atr(high, low, close, 14)
            atr_sma = self.sma(atr_full, atr_avg_period)

            adaptive_factors = np.ones(len(close), dtype=float)
            for i in range(len(close)):
                if np.isnan(atr_full[i]) or np.isnan(atr_sma[i]) or atr_sma[i] <= 0:
                    adaptive_factors[i] = 1.0
                else:
                    ratio = atr_full[i] / atr_sma[i]
                    if ratio > 1.5:
                        adaptive_factors[i] = 1.3  # Wider bands in high vol
                    elif ratio < 0.7:
                        adaptive_factors[i] = 0.8  # Tighter bands in low vol
                    else:
                        adaptive_factors[i] = 1.0

        # Compute both SuperTrends
        fast_trend, fast_st = self._compute_supertrend(
            high, low, close, fast_atr_p, fast_mult, adaptive_factors
        )
        slow_trend, slow_st = self._compute_supertrend(
            high, low, close, slow_atr_p, slow_mult, adaptive_factors
        )

        self.add_indicator('fast_trend', fast_trend.astype(float))
        self.add_indicator('fast_st', fast_st)
        self.add_indicator('slow_trend', slow_trend.astype(float))
        self.add_indicator('slow_st', slow_st)

        self._last_trade_bar = -999

    def next(self, i: int) -> Signal:
        fast_trend = self._indicators['fast_trend']
        slow_trend = self._indicators['slow_trend']
        price = self.data['close'].iloc[i]

        if i < 1:
            return Signal(SignalType.HOLD, "", price)
        if (np.isnan(fast_trend[i]) or np.isnan(slow_trend[i]) or
            np.isnan(fast_trend[i-1]) or np.isnan(slow_trend[i-1])):
            return Signal(SignalType.HOLD, "", price)

        cooldown = self.get_param('cooldown_bars', 10)
        pos = self.get_position()

        ft_curr = int(fast_trend[i])
        st_curr = int(slow_trend[i])
        ft_prev = int(fast_trend[i-1])
        st_prev = int(slow_trend[i-1])

        # ── EXIT: either SuperTrend flips → close position ──
        if pos == 1:
            fast_flipped = ft_prev == 1 and ft_curr == -1
            slow_flipped = st_prev == 1 and st_curr == -1
            if fast_flipped or slow_flipped:
                reason_parts = []
                if fast_flipped:
                    reason_parts.append("快ST转空")
                if slow_flipped:
                    reason_parts.append("慢ST转空")
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"退出多头: {', '.join(reason_parts)}")
            return Signal(SignalType.HOLD, "", price)

        if pos == -1:
            fast_flipped = ft_prev == -1 and ft_curr == 1
            slow_flipped = st_prev == -1 and st_curr == 1
            if fast_flipped or slow_flipped:
                reason_parts = []
                if fast_flipped:
                    reason_parts.append("快ST转多")
                if slow_flipped:
                    reason_parts.append("慢ST转多")
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"退出空头: {', '.join(reason_parts)}")
            return Signal(SignalType.HOLD, "", price)

        # ── ENTRY: both STs must agree ──
        if i - self._last_trade_bar < cooldown:
            return Signal(SignalType.HOLD, "", price)

        fast_turned_up = ft_prev == -1 and ft_curr == 1
        slow_turned_up = st_prev == -1 and st_curr == 1
        fast_turned_down = ft_prev == 1 and ft_curr == -1
        slow_turned_down = st_prev == 1 and st_curr == -1

        if fast_turned_up and slow_turned_up:
            self._last_trade_bar = i
            return Signal(SignalType.BUY, "", price,
                          reason=f"双重确认做多: 快ST+慢ST同步转多")
        elif fast_turned_down and slow_turned_down:
            self._last_trade_bar = i
            return Signal(SignalType.SELL, "", price,
                          reason=f"双重确认做空: 快ST+慢ST同步转空")
        elif ft_curr == 1 and st_curr == 1 and (fast_turned_up or slow_turned_up):
            self._last_trade_bar = i
            return Signal(SignalType.BUY, "", price,
                          reason=f"确认做多: 快ST+慢ST均为多头")
        elif ft_curr == -1 and st_curr == -1 and (fast_turned_down or slow_turned_down):
            self._last_trade_bar = i
            return Signal(SignalType.SELL, "", price,
                          reason=f"确认做空: 快ST+慢ST均为空头")

        return Signal(SignalType.HOLD, "", price)
