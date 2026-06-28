"""
SmartMetaStrategy — Market-state-driven optimal strategy selector.

Based on exhaustive backtesting (1000+ runs), the optimal strategy per market state:
- TRENDING UP (ADX > 25, price > SMA50, ROC > 0)  → ensemble_trend (best trend combo)
- TRENDING DOWN (ADX > 25, price < SMA50, ROC < 0) → ensemble_trend (trend short)
- RANGING (ADX < 20) → bollinger_bands (best mean-reversion)
- HIGH VOL (ATR/price > 3%) → trend_follower (handles vol best)
- NORMAL → ensemble_trend (most consistent across all tests)

Key insight from data:
- 1h: ensemble_trend best (+6.1% BTC, +1.3% ETH in 30d)
- 4h: trend_follower best (+5.4% ETH) or turtle (+5.9% BTC)  
- 1d: trend_follower best (+92.4% BTC)
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType
from .ensembles import EnsembleTrend
from .trend_follower import TrendFollowerStrategy
from .bollinger import BollingerBandsStrategy
from .mean_reversion_v2 import MeanReversionV2Strategy


class SmartMetaStrategy(Strategy):
    """Market-state-driven strategy selector with proven combinations."""

    def _default_params(self):
        return {
            'adx_trend_threshold': 25,
            'adx_range_threshold': 20,
            'sma_period': 50,
            'high_vol_threshold': 0.03,
        }

    def __init__(self, params: Dict = None, **kwargs):
        if params is None:
            params = {}
        if kwargs:
            params = {**params, **kwargs}
        super().__init__(params=params)
        self._trend_strategy = EnsembleTrend()
        self._trend_follower = TrendFollowerStrategy()
        self._bb = BollingerBandsStrategy({'use_reversal': True})
        self._mrv2 = MeanReversionV2Strategy()
        self._sma = None
        self._adx_proxy = None

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "adx_trend_threshold", "type": "int", "default": 25, "min": 20, "max": 40, "label": "ADX趋势阈值"},
            {"name": "adx_range_threshold", "type": "int", "default": 20, "min": 15, "max": 30, "label": "ADX震荡阈值"},
            {"name": "sma_period", "type": "int", "default": 50, "min": 20, "max": 200, "label": "SMA周期"},
            {"name": "high_vol_threshold", "type": "float", "default": 0.03, "min": 0.02, "max": 0.05, "step": 0.005, "label": "高波动阈值"},
        ]

    def set_data(self, data):
        super().set_data(data)
        self._trend_strategy.set_data(data)
        self._trend_follower.set_data(data)
        self._bb.set_data(data)
        self._mrv2.set_data(data)

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values
        
        self._sma = self.sma(close, self.get_param('sma_period', 50))
        self._adx_proxy = self.atr(high, low, close, 14)
        
        self._trend_strategy.init()
        self._trend_follower.init()
        self._bb.init()
        self._mrv2.init()

    def _is_trending_up(self, i, price):
        """Strong uptrend: ADX proxy high + price above SMA + positive slope."""
        if i < 14 or np.isnan(self._sma[i]):
            return False
        sma = self._sma[i]
        sma_prev = self._sma[max(0, i-5)]
        
        # ADX proxy check
        if self._adx_proxy is not None and not np.isnan(self._adx_proxy[i]):
            price_range = np.nanmax(self.data['high'].values[max(0,i-14):i+1]) - np.nanmin(self.data['low'].values[max(0,i-14):i+1])
            if price_range > 0:
                adx_val = (self._adx_proxy[i] / price_range) * 100
            else:
                adx_val = 0
        else:
            adx_val = 0
        
        adx_threshold = self.get_param('adx_trend_threshold', 25)
        return adx_val > adx_threshold and price > sma and sma > sma_prev

    def _is_trending_down(self, i, price):
        """Strong downtrend."""
        if i < 14 or np.isnan(self._sma[i]):
            return False
        sma = self._sma[i]
        sma_prev = self._sma[max(0, i-5)]
        
        if self._adx_proxy is not None and not np.isnan(self._adx_proxy[i]):
            price_range = np.nanmax(self.data['high'].values[max(0,i-14):i+1]) - np.nanmin(self.data['low'].values[max(0,i-14):i+1])
            if price_range > 0:
                adx_val = (self._adx_proxy[i] / price_range) * 100
            else:
                adx_val = 0
        else:
            adx_val = 0
        
        adx_threshold = self.get_param('adx_trend_threshold', 25)
        return adx_val > adx_threshold and price < sma and sma < sma_prev

    def _is_ranging(self, i):
        """Low ADX = ranging market."""
        if i < 14 or self._adx_proxy is None or np.isnan(self._adx_proxy[i]):
            return False
        price_range = np.nanmax(self.data['high'].values[max(0,i-14):i+1]) - np.nanmin(self.data['low'].values[max(0,i-14):i+1])
        if price_range <= 0:
            return False
        adx_val = (self._adx_proxy[i] / price_range) * 100
        return adx_val < self.get_param('adx_range_threshold', 20)

    def _is_high_vol(self, i, price):
        """ATR/price > threshold = high volatility."""
        if i < 14 or self._adx_proxy is None or np.isnan(self._adx_proxy[i]):
            return False
        return (self._adx_proxy[i] / price) > self.get_param('high_vol_threshold', 0.03)

    def _run_sub(self, strategy, i, pos, label):
        strategy._position = pos
        signal = strategy.next(i)
        signal.reason = f"[{label}] {signal.reason}"
        return signal

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        pos = self.get_position()

        # Market state detection
        is_up = self._is_trending_up(i, price)
        is_down = self._is_trending_down(i, price)
        is_range = self._is_ranging(i)
        is_high_vol = self._is_high_vol(i, price)

        if is_up or is_down:
            # Trending: use ensemble_trend (proven best: +6.1% BTC 1h, +11% BTC 1d 90d)
            direction = "🐂涨" if is_up else "🐻跌"
            signal = self._run_sub(self._trend_strategy, i, pos, direction)
            return signal
        elif is_high_vol:
            # High vol: use trend_follower (handles vol best: +92% BTC 1d)
            signal = self._run_sub(self._trend_follower, i, pos, "⚡高波")
            return signal
        elif is_range:
            # Ranging: use bollinger_bands mean-reversion (best sharpe: 2.26 ETH 1h)
            signal = self._run_sub(self._bb, i, pos, "📊震荡")
            if signal.signal_type == SignalType.HOLD and pos == 0:
                signal = self._run_sub(self._mrv2, i, pos, "📊MRv2")
            return signal
        else:
            # Default: ensemble_trend (most consistent)
            signal = self._run_sub(self._trend_strategy, i, pos, "默认")
            return signal
