"""
MACD Strategy v4 — Trend Confirmation with Early Exit
==============================================
MACD as a **trend strength meter**, not a standalone crossover strategy.

Entry conditions:
  Long:  MACD > signal AND MACD > 0 AND histogram increasing for 2+ consecutive bars
         AND price > EMA50 (trend direction filter)
  Short: MACD < signal AND MACD < 0 AND histogram expanding (more negative) for 2+
         consecutive bars AND price < EMA50

Exit conditions (v4 improvement — earlier exits):
  Long:  MACD crosses below signal line (momentum lost) OR histogram contracts 2 bars
  Short: MACD crosses above signal line (momentum lost) OR histogram contracts 2 bars
  Fallback: MACD crosses zero (trend exhaustion) as final safety exit

Kept simple: 5 params max.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType


class MACDStrategy(Strategy):
    """MACD Trend Confirmation Filter.

    Entry requires:
    - MACD/signal alignment (MACD > signal for long, < for short)
    - MACD in the right hemisphere (above zero for long, below zero for short)
    - Histogram expansion for consecutive bars (momentum building)
    - Price relative to EMA (trend direction filter)

    Exit (v4): Signal-line crossover first (early), zero-cross as fallback.
    """

    def _default_params(self):
        return {
            'fast_period': 12,
            'slow_period': 26,
            'signal_period': 9,
            'ema_period': 50,
            'hist_bars': 2,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "fast_period", "type": "int", "default": 12, "min": 5, "max": 50, "label": "快线周期"},
            {"name": "slow_period", "type": "int", "default": 26, "min": 10, "max": 100, "label": "慢线周期"},
            {"name": "signal_period", "type": "int", "default": 9, "min": 3, "max": 30, "label": "信号线周期"},
            {"name": "ema_period", "type": "int", "default": 50, "min": 20, "max": 200, "label": "EMA趋势周期"},
            {"name": "hist_bars", "type": "int", "default": 2, "min": 1, "max": 5, "label": "柱状连续扩张K线数"},
        ]

    def init(self):
        close = self.data['close'].values
        fast_p = self.get_param('fast_period', 12)
        slow_p = self.get_param('slow_period', 26)
        sig_p = self.get_param('signal_period', 9)
        ema_p = self.get_param('ema_period', 50)

        # MACD line = fast EMA - slow EMA
        fast_ema = self.ema(close, fast_p)
        slow_ema = self.ema(close, slow_p)
        macd_line = fast_ema - slow_ema

        # Signal line = EMA of MACD line
        signal_line = self.ema(macd_line, sig_p)

        # Histogram = MACD - signal
        histogram = macd_line - signal_line

        self.add_indicator('macd', macd_line)
        self.add_indicator('signal', signal_line)
        self.add_indicator('histogram', histogram)
        self.add_indicator('ema_trend', self.ema(close, ema_p))

    def _crosses(self, series, i, direction):
        """Check if series crosses at index i.
        direction: +1 = crosses above (was <=0, now >0)
                   -1 = crosses below (was >=0, now <0)"""
        if i < 1:
            return False
        if np.isnan(series[i]) or np.isnan(series[i-1]):
            return False
        if direction == 1:
            return series[i-1] <= 0 and series[i] > 0
        return series[i-1] >= 0 and series[i] < 0

    def _crosses_zero_above(self, series: np.ndarray, i: int) -> bool:
        return self._crosses(series, i, 1)

    def _crosses_zero_below(self, series: np.ndarray, i: int) -> bool:
        return self._crosses(series, i, -1)

    def _histogram_contracting(self, hist, i, bars=2):
        """True if histogram has been shrinking (losing momentum) for `bars` bars."""
        if i < bars:
            return False
        for j in range(i - bars + 1, i + 1):
            prev = j - 1
            if np.isnan(hist[j]) or np.isnan(hist[prev]):
                return False
            if abs(hist[j]) >= abs(hist[prev]):
                return False
        return True

    def _histogram_expanding_consecutive(self, hist: np.ndarray, i: int, direction: int) -> bool:
        """True if histogram has been expanding in `direction` for `hist_bars` consecutive bars.

        direction: +1 for long (histogram increasingly positive), -1 for short (increasingly negative).
        """
        bars = self.get_param('hist_bars', 2)
        if i < bars:
            return False

        for j in range(bars):
            idx = i - bars + 1 + j
            prev = idx - 1
            if np.isnan(hist[idx]) or np.isnan(hist[prev]):
                return False
            if direction == 1:
                if hist[idx] <= 0 or hist[idx] <= hist[prev]:
                    return False
            else:
                if hist[idx] >= 0 or hist[idx] >= hist[prev]:
                    return False
        return True

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        macd = self._indicators['macd']
        signal = self._indicators['signal']
        hist = self._indicators['histogram']
        ema_trend = self._indicators['ema_trend']

        if np.isnan(macd[i]) or np.isnan(signal[i]):
            return Signal(SignalType.HOLD, "", price)

        pos = self.get_position()
        price_above_ema = not np.isnan(ema_trend[i]) and price > ema_trend[i]
        price_below_ema = not np.isnan(ema_trend[i]) and price < ema_trend[i]

        # --- Entry logic ---
        if pos == 0:
            # Long: MACD > signal AND MACD > 0 AND histogram expanding AND price > EMA
            if (macd[i] > signal[i] and macd[i] > 0 and
                self._histogram_expanding_consecutive(hist, i, direction=1) and
                price_above_ema):
                return Signal(SignalType.BUY, "", price,
                              reason=f"MACD趋势确认做多(macd={macd[i]:.4f},hist={hist[i]:.4f},price>ema)")

            # Short: MACD < signal AND MACD < 0 AND histogram expanding AND price < EMA
            if (macd[i] < signal[i] and macd[i] < 0 and
                self._histogram_expanding_consecutive(hist, i, direction=-1) and
                price_below_ema):
                return Signal(SignalType.SELL, "", price,
                              reason=f"MACD趋势确认做空(macd={macd[i]:.4f},hist={hist[i]:.4f},price<ema)")

            return Signal(SignalType.HOLD, "", price)

        # --- Exit logic (v6: four-level exit system) ---
        # Level 1 (WARNING): MACD < signal line → just note, don't exit
        # Level 2 (ALERT): Signal cross + histogram 0 or negative → alert
        # Level 3 (EXIT): Signal cross + histogram contracting 3 bars → confirmed exit  
        # Level 4 (FORCED): MACD crosses zero → trend exhausted, must exit
        if pos == 1:
            signal_cross = macd[i-1] >= signal[i-1] and macd[i] < signal[i]
            hist_contracting_3 = self._histogram_contracting(hist, i, bars=3)
            zero_cross = self._crosses_zero_below(macd, i)

            # Level 4: zero cross = forced exit (no questions asked)
            if zero_cross:
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"MACD强制退出:零轴交叉(macd={macd[i]:.4f})")
            # Level 3: signal cross AND sustained histogram contraction (3 bars)
            if signal_cross and hist_contracting_3:
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"MACD确认退出:信号交叉+持续收缩(macd={macd[i]:.4f})")

        elif pos == -1:
            signal_cross = macd[i-1] <= signal[i-1] and macd[i] > signal[i]
            hist_contracting_3 = self._histogram_contracting(hist, i, bars=3)
            zero_cross = self._crosses_zero_above(macd, i)

            if zero_cross:
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"MACD强制退出:零轴交叉(macd={macd[i]:.4f})")
            if signal_cross and hist_contracting_3:
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"MACD确认退出:信号交叉+持续收缩(macd={macd[i]:.4f})")

        return Signal(SignalType.HOLD, "", price)
