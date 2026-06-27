"""
Enhanced Mean-Reversion Strategy V2

Improvements over v1:
- Momentum divergence detection: Price makes new low but ROC is rising
  (bullish divergence) → stronger BUY signal
- Volatility regime filter: Only mean-revert when ATR is within normal
  range (not panic, not dead)
- Signal strength scoring: Combine RSI extremity + BB position + volume
  surge into a 0-3 score
- Only trade when score >= 2
- Exit when score drops to 0

Designed for the 18% of crypto periods that are ranging, with
volatility-aware filtering to avoid whipsaws.
"""
from typing import Dict, List
import numpy as np
import pandas as pd
from .base import Strategy, Signal, SignalType


class MeanReversionV2Strategy(Strategy):
    """Enhanced mean-reversion with divergence detection, volatility regime
    filter, and multi-factor signal strength scoring.

    Entry: Score >= 2 (RSI extreme + BB extreme + volume surge)
    Exit:  Score drops to 0 or ATR-based trailing stop
    """

    def _default_params(self):
        return {
            'rsi_period': 10,
            'rsi_oversold': 35,
            'rsi_overbought': 75,
            'bb_period': 20,
            'bb_std': 1.5,
            'vol_min': 0.003,
            'vol_max': 0.06,
            'min_score': 2,
            'volume_period': 20,
            'atr_period': 14,
            'atr_exit_mult': 1.0,
            'use_atr_exit': True,
            'divergence_lookback': 20,
            'use_adx_trend_filter': True,
            'adx_trend_threshold': 30,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self._entry_score: int = 0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "rsi_period", "type": "int", "default": 10, "min": 5, "max": 50, "label": "RSI周期"},
            {"name": "rsi_oversold", "type": "int", "default": 35, "min": 10, "max": 40, "label": "超卖阈值"},
            {"name": "rsi_overbought", "type": "int", "default": 75, "min": 60, "max": 90, "label": "超买阈值"},
            {"name": "bb_period", "type": "int", "default": 20, "min": 10, "max": 50, "label": "布林带周期"},
            {"name": "bb_std", "type": "float", "default": 1.5, "min": 1.0, "max": 3.0, "step": 0.5, "label": "布林带标准差"},
            {"name": "vol_min", "type": "float", "default": 0.003, "min": 0.001, "max": 0.01, "step": 0.001, "label": "最小波动率"},
            {"name": "vol_max", "type": "float", "default": 0.06, "min": 0.02, "max": 0.15, "step": 0.005, "label": "最大波动率"},
            {"name": "min_score", "type": "int", "default": 2, "min": 1, "max": 3, "label": "最低入场评分"},
            {"name": "volume_period", "type": "int", "default": 20, "min": 5, "max": 50, "label": "成交量均线周期"},
            {"name": "atr_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "ATR周期"},
            {"name": "atr_exit_mult", "type": "float", "default": 1.5, "min": 0.5, "max": 3.0, "step": 0.5, "label": "ATR退出倍数"},
            {"name": "use_atr_exit", "type": "bool", "default": True, "label": "ATR退出"},
            {"name": "divergence_lookback", "type": "int", "default": 20, "min": 5, "max": 50, "label": "背离回溯期"},
        ]

    def _compute_volume_surge(self, volume, period, i):
        """Check if current volume is significantly above average."""
        if i < period:
            return 0
        avg_vol = np.nanmean(volume[i - period:i])
        if avg_vol <= 0:
            return 0
        ratio = volume[i] / avg_vol
        if ratio > 1.5:
            return 1
        return 0

    def _compute_bb_position_score(self, price, lower, upper, middle, i):
        """Score BB position: 1 if price is outside bands."""
        if np.isnan(lower[i]) or np.isnan(upper[i]):
            return 0
        if price <= lower[i] or price >= upper[i]:
            return 1
        return 0

    def _compute_rsi_score(self, rsi, i):
        """Score RSI extremity: 1 if oversold or overbought."""
        if np.isnan(rsi[i]):
            return 0
        oversold = self.get_param('rsi_oversold', 35)
        overbought = self.get_param('rsi_overbought', 75)
        if rsi[i] <= oversold or rsi[i] >= overbought:
            return 1
        return 0

    def _detect_divergence(self, close, roc, i):
        """Detect momentum divergence.

        Bullish: price makes new low but ROC is rising (higher low in momentum)
        Bearish: price makes new high but ROC is falling (lower high in momentum)

        Returns:
            'bullish', 'bearish', or ''
        """
        lookback = self.get_param('divergence_lookback', 20)
        if i < lookback + 1:
            return ''

        window_start = i - lookback
        mid = window_start + lookback // 2

        # Compare two halves of the lookback window
        first_roc = roc[window_start:mid]
        second_roc = roc[mid:i + 1]
        first_price = close[window_start:mid]
        second_price = close[mid:i + 1]

        if (np.all(np.isnan(first_roc)) or np.all(np.isnan(second_roc)) or
            np.all(np.isnan(first_price)) or np.all(np.isnan(second_price))):
            return ''

        first_price_low = np.nanmin(first_price)
        first_roc_low = np.nanmin(first_roc)
        second_price_low = np.nanmin(second_price)
        second_roc_low = np.nanmin(second_roc)
        first_price_high = np.nanmax(first_price)
        first_roc_high = np.nanmax(first_roc)
        second_price_high = np.nanmax(second_price)
        second_roc_high = np.nanmax(second_roc)

        # Bullish divergence: price lower low, ROC higher low
        if (second_price_low < first_price_low * 0.999 and
            second_roc_low > first_roc_low):
            return 'bullish'

        # Bearish divergence: price higher high, ROC lower high
        if (second_price_high > first_price_high * 1.001 and
            second_roc_high < first_roc_high):
            return 'bearish'

        return ''

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values
        volume = self.data['volume'].values if 'volume' in self.data.columns else None
        n = len(close)

        if n < 15:
            self.add_indicator('rsi', np.full(n, np.nan))
            self.add_indicator('bb_lower', np.full(n, np.nan))
            self.add_indicator('bb_upper', np.full(n, np.nan))
            self.add_indicator('bb_middle', np.full(n, np.nan))
            self.add_indicator('atr', np.full(n, np.nan))
            self.add_indicator('vol_ratio', np.full(n, np.nan))
            self.add_indicator('volume_sma', np.full(n, np.nan))
            self.add_indicator('roc', np.full(n, np.nan))
            return

        # RSI
        rsi_period = self.get_param('rsi_period', 10)
        rsi = self.rsi(close, rsi_period)
        self.add_indicator('rsi', rsi)

        # Bollinger Bands
        bb_period = self.get_param('bb_period', 20)
        bb_std = self.get_param('bb_std', 1.5)
        bb_middle, bb_upper, bb_lower = self.bollinger_bands(close, bb_period, bb_std)
        self.add_indicator('bb_lower', bb_lower)
        self.add_indicator('bb_upper', bb_upper)
        self.add_indicator('bb_middle', bb_middle)

        # ATR
        atr_period = self.get_param('atr_period', 14)
        atr = self.atr(high, low, close, atr_period)
        self.add_indicator('atr', atr)

        # Volatility ratio: ATR / price
        vol_ratio = np.full(n, np.nan, dtype=float)
        for j in range(n):
            if not np.isnan(atr[j]) and close[j] > 0:
                vol_ratio[j] = atr[j] / close[j]
        self.add_indicator('vol_ratio', vol_ratio)

        # Volume SMA (for surge detection)
        if volume is not None:
            vol_period = self.get_param('volume_period', 20)
            volume_sma = self.sma(volume, vol_period)
            self.add_indicator('volume_sma', volume_sma)
        else:
            self.add_indicator('volume_sma', np.full(n, np.nan))

        # ROC for divergence detection
        roc = np.full(n, np.nan, dtype=float)
        roc_period = self.get_param('rsi_period', 10)  # Use same period as RSI
        for j in range(roc_period, n):
            if close[j - roc_period] != 0:
                roc[j] = (close[j] - close[j - roc_period]) / close[j - roc_period]
        self.add_indicator('roc', roc)

        self._entry_score = 0

    def _compute_signal_score(self, i: int, direction: str) -> int:
        """Compute signal strength score (0-3) for a given direction.

        Components:
        1. RSI extremity (+1)
        2. BB position (+1)
        3. Volume surge (+1)
        """
        rsi = self._indicators.get('rsi')
        bb_lower = self._indicators.get('bb_lower')
        bb_upper = self._indicators.get('bb_upper')
        bb_middle = self._indicators.get('bb_middle')
        volume = self.data['volume'].values if 'volume' in self.data.columns else None

        score = 0
        reasons = []

        # RSI extremity
        rsi_score = self._compute_rsi_score(rsi, i)
        if rsi_score:
            rsi_val = rsi[i]
            if direction == 'long' and rsi_val <= self.get_param('rsi_oversold', 35):
                score += 1
                reasons.append(f"RSI超卖({rsi_val:.1f})")
            elif direction == 'short' and rsi_val >= self.get_param('rsi_overbought', 75):
                score += 1
                reasons.append(f"RSI超买({rsi_val:.1f})")

        # BB position
        price = self.data['close'].iloc[i]
        bb_score = self._compute_bb_position_score(price, bb_lower, bb_upper, bb_middle, i)
        if bb_score:
            if direction == 'long' and price <= bb_lower[i]:
                score += 1
                reasons.append(f"价格破下轨({price:.2f})")
            elif direction == 'short' and price >= bb_upper[i]:
                score += 1
                reasons.append(f"价格破上轨({price:.2f})")

        # Volume surge
        if volume is not None:
            vol_period = self.get_param('volume_period', 20)
            vol_surge = self._compute_volume_surge(volume, vol_period, i)
            if vol_surge:
                score += 1
                reasons.append("成交量放量")

        return score

    def _in_vol_regime(self, vol_ratio, i):
        """Check if volatility is within normal range for mean reversion."""
        if np.isnan(vol_ratio[i]):
            return False
        vol_min = self.get_param('vol_min', 0.003)
        vol_max = self.get_param('vol_max', 0.06)
        return vol_min <= vol_ratio[i] <= vol_max

    def next(self, i: int) -> Signal:
        rsi = self._indicators.get('rsi')
        atr = self._indicators.get('atr')
        vol_ratio = self._indicators.get('vol_ratio')
        roc = self._indicators.get('roc')
        close = self.data['close'].values
        price = close[i]

        if (rsi is None or np.isnan(rsi[i]) or
            atr is None or np.isnan(atr[i])):
            return Signal(SignalType.HOLD, "", price)

        pos = self.get_position()

        # ADX trend filter: suspend mean-reversion in trending markets
        if pos == 0 and self.get_param('use_adx_trend_filter', True):
            if self.is_trending_adx(
                self.data['high'].values, self.data['low'].values, self.data['close'].values,
                i, period=14, threshold=self.get_param('adx_trend_threshold', 25)
            ):
                return Signal(SignalType.HOLD, "", price, reason="ADX趋势过滤:暂停均值回归")
        use_atr_exit = self.get_param('use_atr_exit', True)

        # --- Volatility regime filter ---
        in_vol_regime = self._in_vol_regime(vol_ratio, i)

        # --- EXIT: ATR-based trailing stop ---
        if pos != 0 and use_atr_exit:
            entry_price = self._entry_price
            atr_val = atr[i]
            atr_exit_mult = self.get_param('atr_exit_mult', 1.5)

            if pos == 1:
                if price <= entry_price - atr_exit_mult * atr_val:
                    self._entry_score = 0
                    return Signal(SignalType.CLOSE_LONG, "", price,
                                  reason=f"ATR止损触发 @ {price:.2f} (ATR={atr_val:.2f})")
            elif pos == -1:
                if price >= entry_price + atr_exit_mult * atr_val:
                    self._entry_score = 0
                    return Signal(SignalType.CLOSE_SHORT, "", price,
                                  reason=f"ATR止损触发 @ {price:.2f} (ATR={atr_val:.2f})")

        # --- EXIT: score drops to 0 ---
        if pos == 1:
            current_score = self._compute_signal_score(i, 'long')
            if current_score == 0:
                self._entry_score = 0
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"信号评分归零，平多")
            return Signal(SignalType.HOLD, "", price)

        if pos == -1:
            current_score = self._compute_signal_score(i, 'short')
            if current_score == 0:
                self._entry_score = 0
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"信号评分归零，平空")
            return Signal(SignalType.HOLD, "", price)

        # --- ENTRY: only in normal volatility regime ---
        if not in_vol_regime:
            return Signal(SignalType.HOLD, "", price)

        min_score = self.get_param('min_score', 2)

        # Check long entry
        long_score = self._compute_signal_score(i, 'long')
        if long_score >= min_score:
            # Check divergence for bonus confidence
            divergence = self._detect_divergence(close, roc, i)
            reason = f"均值回归做多(评分{int(long_score)}/3)"
            if divergence == 'bullish':
                reason += " [看涨背离确认]"
            self._entry_score = long_score
            return Signal(SignalType.BUY, "", price, reason=reason)

        # Check short entry
        short_score = self._compute_signal_score(i, 'short')
        if short_score >= min_score:
            divergence = self._detect_divergence(close, roc, i)
            reason = f"均值回归做空(评分{int(short_score)}/3)"
            if divergence == 'bearish':
                reason += " [看跌背离确认]"
            self._entry_score = short_score
            return Signal(SignalType.SELL, "", price, reason=reason)

        return Signal(SignalType.HOLD, "", price)
