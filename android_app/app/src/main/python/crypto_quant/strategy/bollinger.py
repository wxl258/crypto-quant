"""
Bollinger Bands Breakout Strategy
布林带突破策略 - 增强版

Improvements:
- Volatility-adaptive bandwidth: wider bands in high volatility (uses ATR to scale std_dev)
- Volume confirmation: only trade when volume exceeds its moving average
- RSI confirmation filter: require RSI oversold (< rsi_filter_low) for LONG entry
  and RSI overbought (> rsi_filter_high) for SHORT entry. This prevents entering when
  price touches bands but RSI is neutral (likely a breakout, not mean-reversion).
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType


class BollingerBandsStrategy(Strategy):
    """Bollinger Bands Mean Reversion with adaptive bandwidth, volume confirmation, and RSI filter."""

    def _default_params(self):
        return {
            'period': 10,
            'std_dev': 1.5,
            'use_reversal': True,  # True: mean reversion, False: breakout
            'use_adaptive_bandwidth': True,
            'atr_period': 14,
            'vol_expansion_factor': 1.5,
            'use_volume_filter': True,
            'volume_ma_period': 20,
            'volume_threshold': 1.0,  # multiplier over volume MA
            'use_rsi_filter': True,
            'rsi_period': 14,
            'rsi_filter_low': 40,
            'rsi_filter_high': 60,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'use_adx_trend_filter': True,
            'adx_trend_threshold': 30,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "period", "type": "int", "default": 10, "min": 10, "max": 100, "label": "布林带周期"},
            {"name": "std_dev", "type": "float", "default": 1.5, "min": 1.0, "max": 3.0, "step": 0.1, "label": "标准差倍数"},
            {"name": "use_reversal", "type": "bool", "default": True, "label": "均值回归模式"},
            {"name": "use_adaptive_bandwidth", "type": "bool", "default": True, "label": "自适应带宽(波动率调整)"},
            {"name": "atr_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "ATR周期"},
            {"name": "vol_expansion_factor", "type": "float", "default": 1.5, "min": 1.0, "max": 3.0, "step": 0.1, "label": "波动率扩张因子"},
            {"name": "use_volume_filter", "type": "bool", "default": True, "label": "成交量过滤"},
            {"name": "volume_ma_period", "type": "int", "default": 20, "min": 5, "max": 50, "label": "成交量MA周期"},
            {"name": "volume_threshold", "type": "float", "default": 1.0, "min": 0.5, "max": 2.0, "step": 0.1, "label": "成交量阈值(倍)"},
            {"name": "use_rsi_filter", "type": "bool", "default": True, "label": "RSI确认过滤"},
            {"name": "rsi_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "RSI周期"},
            {"name": "rsi_filter_low", "type": "int", "default": 40, "min": 10, "max": 50, "label": "RSI过滤低阈值"},
            {"name": "rsi_filter_high", "type": "int", "default": 60, "min": 50, "max": 90, "label": "RSI过滤高阈值"},
            {"name": "rsi_oversold", "type": "int", "default": 30, "min": 10, "max": 40, "label": "RSI超卖"},
            {"name": "rsi_overbought", "type": "int", "default": 70, "min": 60, "max": 90, "label": "RSI超买"},
        ]

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values
        period = self.get_param('period', 20)
        std_dev = self.get_param('std_dev', 2.0)
        atr_p = self.get_param('atr_period', 14)
        rsi_p = self.get_param('rsi_period', 14)

        middle, upper, lower = self.bollinger_bands(close, period, std_dev)
        self.add_indicator('bb_middle', middle)
        self.add_indicator('bb_upper', upper)
        self.add_indicator('bb_lower', lower)
        self.add_indicator('atr', self.atr(high, low, close, atr_p))

        # RSI for confirmation filter
        self.add_indicator('rsi', self.rsi(close, rsi_p))

        # Volume indicators if volume column exists
        if 'volume' in self.data.columns:
            volume = self.data['volume'].values
            vol_ma_p = self.get_param('volume_ma_period', 20)
            self.add_indicator('volume_ma', self.sma(volume, vol_ma_p))

    def _get_adaptive_std_dev(self, i: int) -> float:
        """Compute adaptive std_dev multiplier based on current volatility."""
        if not self.get_param('use_adaptive_bandwidth', True):
            return self.get_param('std_dev', 2.0)

        atr = self._indicators.get('atr')
        if atr is None or np.isnan(atr[i]):
            return self.get_param('std_dev', 2.0)

        base_std = self.get_param('std_dev', 2.0)
        expansion = self.get_param('vol_expansion_factor', 1.5)

        if i >= 20:
            recent_atr = atr[max(0, i - 20):i+1]
            atr_mean = np.nanmean(recent_atr)
            if atr_mean > 0:
                ratio = atr[i] / atr_mean
                if ratio > 1.0:
                    return base_std * (1.0 + (ratio - 1.0) * expansion)

        return base_std

    def _volume_confirmed(self, i: int) -> bool:
        """Check if current volume exceeds the volume MA threshold."""
        if not self.get_param('use_volume_filter', True):
            return True

        vol_ma = self._indicators.get('volume_ma')
        if vol_ma is None or np.isnan(vol_ma[i]):
            return True  # No volume data, pass through

        if 'volume' not in self.data.columns:
            return True

        current_vol = self.data['volume'].iloc[i]
        threshold = self.get_param('volume_threshold', 1.0)

        return current_vol > vol_ma[i] * threshold

    def _rsi_confirmed_long(self, i: int) -> bool:
        """Check if RSI confirms a long entry."""
        if not self.get_param('use_rsi_filter', True):
            return True

        rsi = self._indicators.get('rsi')
        if rsi is None or np.isnan(rsi[i]):
            return True

        use_reversal = self.get_param('use_reversal', True)
        if use_reversal:
            # Mean-reversion long: RSI must be oversold (dip buying)
            rsi_filter_low = self.get_param('rsi_filter_low', 40)
            return rsi[i] < rsi_filter_low
        else:
            # Breakout long: RSI must be strong (momentum continuation)
            rsi_filter_high = self.get_param('rsi_filter_high', 60)
            return rsi[i] > rsi_filter_high

    def _rsi_confirmed_short(self, i: int) -> bool:
        """Check if RSI confirms a short entry."""
        if not self.get_param('use_rsi_filter', True):
            return True

        rsi = self._indicators.get('rsi')
        if rsi is None or np.isnan(rsi[i]):
            return True

        use_reversal = self.get_param('use_reversal', True)
        if use_reversal:
            # Mean-reversion short: RSI must be overbought (spike selling)
            rsi_filter_high = self.get_param('rsi_filter_high', 60)
            return rsi[i] > rsi_filter_high
        else:
            # Breakout short: RSI must be weak (momentum breakdown)
            rsi_filter_low = self.get_param('rsi_filter_low', 40)
            return rsi[i] < rsi_filter_low

    def next(self, i: int) -> Signal:
        upper = self._indicators['bb_upper']
        lower = self._indicators['bb_lower']
        middle = self._indicators['bb_middle']
        price = self.data['close'].iloc[i]

        if np.isnan(upper[i]) or np.isnan(lower[i]) or np.isnan(middle[i]):
            return Signal(SignalType.HOLD, "", price)

        use_reversal = self.get_param('use_reversal', True)
        pos = self.get_position()

        # ADX trend filter for mean-reversion mode: suspend in trending markets
        if use_reversal and pos == 0 and self.get_param('use_adx_trend_filter', True):
            if self.is_trending_adx(
                self.data['high'].values, self.data['low'].values, self.data['close'].values,
                i, period=14, threshold=self.get_param('adx_trend_threshold', 25)
            ):
                return Signal(SignalType.HOLD, "", price, reason="ADX趋势过滤:暂停均值回归")

        adaptive_std = self._get_adaptive_std_dev(i)
        base_std = self.get_param('std_dev', 2.0)
        band_width_mult = adaptive_std / base_std if base_std > 0 else 1.0

        middle_val = middle[i]
        adjusted_upper = middle_val + (upper[i] - middle_val) * band_width_mult
        adjusted_lower = middle_val - (middle_val - lower[i]) * band_width_mult

        if use_reversal:
            # --- Mean reversion mode ---
            if pos == 0:
                # SELL at upper band (mean reversion short)
                if price >= adjusted_upper:
                    if not self._volume_confirmed(i):
                        return Signal(SignalType.HOLD, "", price)
                    if not self._rsi_confirmed_short(i):
                        return Signal(SignalType.HOLD, "", price)
                    if self.signal_quality_score(i, 'SELL') < 0.75:
                        return Signal(SignalType.HOLD, "", price, reason="信号质量不足")

                    reason = f"触及上轨({adjusted_upper:.2f}, 带宽x{band_width_mult:.2f})，做空"
                    return Signal(SignalType.SELL, "", price, reason=reason)

                # BUY at lower band (mean reversion long)
                elif price <= adjusted_lower:
                    if not self._volume_confirmed(i):
                        return Signal(SignalType.HOLD, "", price)
                    if not self._rsi_confirmed_long(i):
                        return Signal(SignalType.HOLD, "", price)
                    if self.signal_quality_score(i, 'BUY') < 0.75:
                        return Signal(SignalType.HOLD, "", price, reason="信号质量不足")

                    reason = f"触及下轨({adjusted_lower:.2f}, 带宽x{band_width_mult:.2f})，做多"
                    return Signal(SignalType.BUY, "", price, reason=reason)

            elif pos == 1 and price >= middle_val:
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"回归中轨({middle_val:.2f})，平多")

            elif pos == -1 and price <= middle_val:
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"回归中轨({middle_val:.2f})，平空")
        else:
            # --- Breakout mode ---
            if pos == 0:
                if price > adjusted_upper:
                    if not self._volume_confirmed(i):
                        return Signal(SignalType.HOLD, "", price)

                    if not self._rsi_confirmed_long(i):
                        return Signal(SignalType.HOLD, "", price)

                    reason = f"突破上轨({adjusted_upper:.2f}, 带宽x{band_width_mult:.2f})，追多"
                    return Signal(SignalType.BUY, "", price, reason=reason)

                elif price < adjusted_lower:
                    if not self._volume_confirmed(i):
                        return Signal(SignalType.HOLD, "", price)

                    if not self._rsi_confirmed_short(i):
                        return Signal(SignalType.HOLD, "", price)

                    reason = f"跌破下轨({adjusted_lower:.2f}, 带宽x{band_width_mult:.2f})，追空"
                    return Signal(SignalType.SELL, "", price, reason=reason)

            elif pos == 1 and price < middle_val:
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"跌破中轨({middle_val:.2f})，止损")

            elif pos == -1 and price > middle_val:
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"突破中轨({middle_val:.2f})，止损")

        return Signal(SignalType.HOLD, "", price)
