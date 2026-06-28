"""
MetaStrategy v2 — Simplified market-regime strategy.
Uses only 2 strategy families:
- BULL/BEAR → ensemble_trend (best trend-following combo across all tests)
- RANGE → mean_reversion_v2 (best mean-reversion performer)

This simplification reduces state management complexity and leverages
the top-performing strategy from each category.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType
from .regime_analyzer import MarketRegimeAnalyzer
from .ensembles import EnsembleTrend
from .mean_reversion_v2 import MeanReversionV2Strategy
from .trend_follower import TrendFollowerStrategy


class MetaStrategy(Strategy):
    """v2: Simple 2-mode meta-strategy.
    
    Trending (BULL/BEAR) → ensemble_trend (SuperTrend+Turtle+MACD)
    Ranging (RANGE)     → mean_reversion_v2 (RSI+BB+Volume scoring)
    """

    def _default_params(self):
        return {
            'sma_short': 50, 'sma_long': 200,
            'bull_threshold': 0.15, 'bear_threshold': -0.15,
            'min_confidence': 0.5, 'min_regime_bars': 2,
        }

    def __init__(self, params: Dict = None, **kwargs):
        if params is None:
            params = {}
        if kwargs:
            params = {**params, **kwargs}
        super().__init__(params=params)
        self._trend_strategy = EnsembleTrend()
        self._range_strategy = MeanReversionV2Strategy()
        self._analyzer = None
        self._regime_data = None
        self._current_regime = 'range'
        self._bars_in_regime = 0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "sma_short", "type": "int", "default": 50, "min": 20, "max": 200, "label": "短期SMA"},
            {"name": "sma_long", "type": "int", "default": 200, "min": 50, "max": 500, "label": "长期SMA"},
            {"name": "bull_threshold", "type": "float", "default": 0.15, "min": 0.05, "max": 0.4, "step": 0.05, "label": "牛市阈值"},
            {"name": "bear_threshold", "type": "float", "default": -0.15, "min": -0.4, "max": -0.05, "step": 0.05, "label": "熊市阈值"},
            {"name": "min_confidence", "type": "float", "default": 0.5, "min": 0.3, "max": 0.9, "step": 0.1, "label": "最低置信度"},
            {"name": "min_regime_bars", "type": "int", "default": 2, "min": 1, "max": 10, "label": "最小确认K线"},
        ]

    def set_data(self, data):
        super().set_data(data)
        self._analyzer = MarketRegimeAnalyzer(
            sma_short=self.get_param('sma_short', 50),
            sma_long=self.get_param('sma_long', 200),
            bull_threshold=self.get_param('bull_threshold', 0.15),
            bear_threshold=self.get_param('bear_threshold', -0.15),
        )
        self._regime_data = self._analyzer.analyze(data)
        self._trend_strategy.set_data(data)
        self._range_strategy.set_data(data)

    def init(self):
        self._trend_strategy.init()
        self._range_strategy.init()
        self._current_regime = 'range'
        self._bars_in_regime = 0

    def next(self, i: int) -> Signal:
        if self._regime_data is None:
            return Signal(SignalType.HOLD, "", self.data['close'].iloc[i])

        regime = self._regime_data['regime'].iloc[i]
        confidence = self._regime_data['regime_confidence'].iloc[i]
        pos = self.get_position()
        price = self.data['close'].iloc[i]

        # Regime switch with hysteresis
        if regime != self._current_regime:
            self._bars_in_regime += 1
            min_bars = self.get_param('min_regime_bars', 2)
            min_conf = self.get_param('min_confidence', 0.5)
            if self._bars_in_regime >= min_bars and confidence >= min_conf:
                self._current_regime = regime
                self._bars_in_regime = 0
        else:
            self._bars_in_regime = 0

        if regime in ('bull', 'bear'):
            # Trending: use ensemble_trend
            self._trend_strategy._position = pos
            signal = self._trend_strategy.next(i)
            signal.reason = f"[{'🐂' if regime == 'bull' else '🐻'}趋势] {signal.reason}"
            return signal
        else:
            # Range: use mean_reversion_v2
            self._range_strategy._position = pos
            signal = self._range_strategy.next(i)
            signal.reason = f"[📊震荡] {signal.reason}"
            return signal
