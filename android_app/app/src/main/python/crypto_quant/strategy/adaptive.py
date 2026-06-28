"""
Adaptive Ensemble v2 — noise-filtered market regime detection.

Detects market regime (trending vs ranging vs volatile) and selects the best
strategy for current conditions.  Now with noise filters to avoid trading
low-quality markets.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType
from .rsi_mean_reversion import RSIMeanReversionStrategy
from .bollinger import BollingerBandsStrategy
from .supertrend import SuperTrendStrategy


class AdaptiveEnsembleStrategy(Strategy):
    """Market-regime-adaptive strategy ensemble with noise filters.

    Noise filters (applied BEFORE strategy selection):
    - ADX < adx_min_threshold: market is pure noise, HOLD
    - ATR/price < vol_min_threshold: market is dead, HOLD
    - ATR/price > vol_max_threshold: market is panicking, HOLD

    Regime selection (after noise filters pass):
    - High ADX (>25): trending → use SuperTrend (trend following)
    - Low ADX (<20): ranging → use RSI + Bollinger (mean reversion)
    - High volatility (ATR/price > 3%): conservative → use Bollinger only
    """

    def _default_params(self):
        return {
            'adx_period': 14,
            'adx_trend_threshold': 25,
            'adx_range_threshold': 20,
            'volatility_threshold': 0.03,
            'adx_min_threshold': 12,
            'vol_min_threshold': 0.003,
            'vol_max_threshold': 0.08,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self._rsi = RSIMeanReversionStrategy({'rsi_period': 14, 'oversold': 30, 'overbought': 70, 'exit_mid': 50, 'use_mid_exit': True, 'leverage': 3})
        self._bollinger = BollingerBandsStrategy({'period': 20, 'std_dev': 2.0, 'use_reversal': True, 'leverage': 3})
        self._supertrend = SuperTrendStrategy({'fast_atr': 10, 'fast_mult': 2.0, 'slow_atr': 14, 'slow_mult': 3.0, 'cooldown_bars': 5, 'leverage': 3})
        self._active_strategy = None
        self._noise_filtered_bars: int = 0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "adx_period", "type": "int", "default": 14, "min": 7, "max": 50, "label": "ADX周期"},
            {"name": "adx_trend_threshold", "type": "int", "default": 25, "min": 15, "max": 40, "label": "趋势阈值(ADX>)"},
            {"name": "adx_range_threshold", "type": "int", "default": 20, "min": 10, "max": 30, "label": "震荡阈值(ADX<)"},
            {"name": "volatility_threshold", "type": "float", "default": 0.03, "min": 0.01, "max": 0.1, "step": 0.005, "label": "高波动阈值"},
            {"name": "adx_min_threshold", "type": "float", "default": 12.0, "min": 5.0, "max": 25.0, "step": 1.0, "label": "ADX噪声阈值(ADX<)"},
            {"name": "vol_min_threshold", "type": "float", "default": 0.003, "min": 0.001, "max": 0.01, "step": 0.001, "label": "最小波动阈值(ATR/price<)"},
            {"name": "vol_max_threshold", "type": "float", "default": 0.08, "min": 0.04, "max": 0.20, "step": 0.01, "label": "最大波动阈值(ATR/price>)"},
        ]

    def set_data(self, data):
        super().set_data(data)
        self._rsi.set_data(data)
        self._bollinger.set_data(data)
        self._supertrend.set_data(data)

    def init(self):
        self._rsi.init()
        self._bollinger.init()
        self._supertrend.init()

        # Reset noise filter counter
        self._noise_filtered_bars = 0

        # Compute ADX for regime detection
        high = self.data['high'].values
        low = self.data['low'].values
        close = self.data['close'].values
        period = self.get_param('adx_period', 14)

        tr = np.maximum(high - low, np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1))
        ))
        tr[0] = high[0] - low[0]

        atr_adx = np.full(len(close), np.nan, dtype=float)
        atr_adx[period] = np.mean(tr[1:period+1])
        for i in range(period+1, len(close)):
            atr_adx[i] = (atr_adx[i-1] * (period-1) + tr[i]) / period

        plus_dm = np.where((high - np.roll(high, 1)) > (np.roll(low, 1) - low),
                           np.maximum(high - np.roll(high, 1), 0), 0)
        minus_dm = np.where((np.roll(low, 1) - low) > (high - np.roll(high, 1)),
                            np.maximum(np.roll(low, 1) - low, 0), 0)
        plus_dm[0] = minus_dm[0] = 0

        plus_di = np.full(len(close), np.nan, dtype=float)
        minus_di = np.full(len(close), np.nan, dtype=float)
        plus_di[period] = 100 * np.mean(plus_dm[1:period+1]) / atr_adx[period] if atr_adx[period] > 0 else 0
        minus_di[period] = 100 * np.mean(minus_dm[1:period+1]) / atr_adx[period] if atr_adx[period] > 0 else 0
        for i in range(period+1, len(close)):
            plus_di[i] = (plus_di[i-1] * (period-1) + 100 * plus_dm[i] / max(atr_adx[i], 1e-10)) / period
            minus_di[i] = (minus_di[i-1] * (period-1) + 100 * minus_dm[i] / max(atr_adx[i], 1e-10)) / period

        dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
        adx = np.full(len(close), np.nan, dtype=float)
        adx[period*2-1] = np.mean(dx[period:period*2])
        for i in range(period*2, len(close)):
            adx[i] = (adx[i-1] * (period-1) + dx[i]) / period

        self.add_indicator('adx', adx)
        self.add_indicator('atr_adx', atr_adx)
        self.add_indicator('plus_di', plus_di)
        self.add_indicator('minus_di', minus_di)

    def _is_noise(self, i: int) -> bool:
        """Check if current market is noise and should not be traded.

        Returns True if ANY noise filter triggers:
        - ADX < adx_min_threshold: pure noise, no directional movement
        - ATR/price < vol_min_threshold: dead market, no volatility
        - ATR/price > vol_max_threshold: panic market, extreme volatility
        """
        adx = self._indicators.get('adx')
        atr = self._indicators.get('atr_adx')
        price = self.data['close'].iloc[i]

        if adx is None or atr is None or price <= 0:
            return True

        if np.isnan(adx[i]) or np.isnan(atr[i]):
            return True

        adx_val = adx[i]
        atr_val = atr[i]
        vol_ratio = atr_val / price

        adx_min = self.get_param('adx_min_threshold', 12)
        vol_min = self.get_param('vol_min_threshold', 0.003)
        vol_max = self.get_param('vol_max_threshold', 0.08)

        # ADX too low → pure noise
        if adx_val < adx_min:
            return True

        # Volatility too low → dead market
        if vol_ratio < vol_min:
            return True

        # Volatility too high → panic market
        if vol_ratio > vol_max:
            return True

        return False

    def _select_strategy(self, i: int) -> Strategy:
        """Select active strategy based on market regime."""
        adx = self._indicators['adx']
        atr = self._indicators['atr_adx']
        price = self.data['close'].iloc[i]

        if np.isnan(adx[i]):
            return self._bollinger  # default

        adx_val = adx[i]
        atr_val = atr[i] if not np.isnan(atr[i]) else 0
        vol_ratio = atr_val / price if price > 0 else 0
        vol_threshold = self.get_param('volatility_threshold', 0.03)
        trend_threshold = self.get_param('adx_trend_threshold', 25)
        range_threshold = self.get_param('adx_range_threshold', 20)

        # High volatility → conservative (Bollinger with wide bands)
        if vol_ratio > vol_threshold:
            return self._bollinger

        # Strong trend → trend following
        if adx_val > trend_threshold:
            return self._supertrend

        # Ranging market → mean reversion
        if adx_val < range_threshold:
            return self._rsi

        # Default: Bollinger
        return self._bollinger

    def next(self, i: int) -> Signal:
        # --- Noise filter: check BEFORE strategy selection ---
        if self._is_noise(i):
            self._noise_filtered_bars += 1
            return Signal(SignalType.HOLD, "", self.data["close"].iloc[i],
                         reason=f"噪声过滤(ADX/波动不满足,已过滤{self._noise_filtered_bars}根K线)")

        active = self._select_strategy(i)
        # Sync position state so sub-strategy knows about portfolio position
        active._position = self.get_position()
        return active.next(i)
