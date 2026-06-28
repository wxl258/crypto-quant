"""
Regime-Adaptive Strategy — Detects market regime and switches between optimal strategies.

- BULL: Use trend-following (SuperTrend + TrendFollower + Turtle)
- BEAR: Use mean-reversion shorts only (RSI + Bollinger short side) + Grid
- RANGE: Use full mean-reversion (RSI + Bollinger + Grid)

v2.0: Fixed bull regime to actually use trend-following strategies
      (previously incorrectly used mean-reversion RSI+BB in bull markets)
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType
from .regime_analyzer import MarketRegimeAnalyzer
from .rsi_mean_reversion import RSIMeanReversionStrategy
from .bollinger import BollingerBandsStrategy
from .grid import GridStrategy
from .supertrend import SuperTrendStrategy
from .trend_follower import TrendFollowerStrategy
from .macd import MACDStrategy


class RegimeAdaptiveStrategy(Strategy):
    """Market-regime-adaptive strategy that switches between strategy sets.
    
    Uses multi-indicator consensus (90d return + SMA alignment + ADX) to detect
    bull/bear/range regimes, then applies the optimal strategy for each.
    """

    def __init__(self, params: Dict = None, **kwargs):
        merged = {
            'sma_short': 50, 'sma_long': 200,
            'bull_threshold': 0.15, 'bear_threshold': -0.15,
            'min_confidence': 0.6,
            'min_regime_bars': 3,
        }
        if params:
            merged.update(params)
        if kwargs:
            merged = {**merged, **kwargs}
        super().__init__(params=merged)
        self._analyzer = None
        self._regime_data = None

        # Strategy instances for each regime
        # BULL: trend-following strategies
        self._bull_supertrend = SuperTrendStrategy()
        self._bull_trend = TrendFollowerStrategy()
        # BEAR: mean-reversion shorts + grid
        self._bear_rsi = RSIMeanReversionStrategy()
        self._bear_grid = GridStrategy()
        # RANGE: mean-reversion
        self._range_rsi = RSIMeanReversionStrategy()
        self._range_bb = BollingerBandsStrategy()
        self._range_grid = GridStrategy()

        # Track active regime
        self._current_regime = 'range'
        self._regime_switches = 0
        self._bars_in_regime = 0  # hysteresis: don't switch too fast

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "sma_short", "type": "int", "default": 50, "min": 20, "max": 200, "label": "短期SMA"},
            {"name": "sma_long", "type": "int", "default": 200, "min": 50, "max": 500, "label": "长期SMA"},
            {"name": "bull_threshold", "type": "float", "default": 0.15, "min": 0.05, "max": 0.4, "step": 0.05, "label": "牛市阈值"},
            {"name": "bear_threshold", "type": "float", "default": -0.15, "min": -0.4, "max": -0.05, "step": 0.05, "label": "熊市阈值"},
        ]

    def set_data(self, data):
        super().set_data(data)
        # Run regime analysis
        self._analyzer = MarketRegimeAnalyzer(
            sma_short=self.get_param('sma_short', 50),
            sma_long=self.get_param('sma_long', 200),
            bull_threshold=self.get_param('bull_threshold', 0.15),
            bear_threshold=self.get_param('bear_threshold', -0.15),
        )
        self._regime_data = self._analyzer.analyze(data)

        # Set data for all sub-strategies
        self._bull_supertrend.set_data(data)
        self._bull_trend.set_data(data)
        self._bear_rsi.set_data(data)
        self._bear_grid.set_data(data)
        self._range_rsi.set_data(data)
        self._range_bb.set_data(data)
        self._range_grid.set_data(data)

    def init(self):
        self._bull_supertrend.init()
        self._bull_trend.init()
        self._bear_rsi.init()
        self._bear_grid.init()
        self._range_rsi.init()
        self._range_bb.init()
        self._range_grid.init()
        self._current_regime = 'range'
        self._bars_in_regime = 0

    def _run_sub_strategy(self, strategy, i, pos, label):
        """Run a sub-strategy with proper position sync and label."""
        strategy._position = pos
        signal = strategy.next(i)
        signal.reason = f"[{label}] {signal.reason}"
        return signal

    def next(self, i: int) -> Signal:
        if self._regime_data is None:
            return Signal(SignalType.HOLD, "", self.data['close'].iloc[i])

        regime = self._regime_data['regime'].iloc[i]
        confidence = self._regime_data['regime_confidence'].iloc[i]
        pos = self.get_position()
        price = self.data['close'].iloc[i]

        # Track regime changes with hysteresis + confidence gate
        if regime != self._current_regime:
            self._bars_in_regime += 1
            min_bars = self.get_param('min_regime_bars', 3)
            min_conf = self.get_param('min_confidence', 0.6)
            # Only switch if: enough bars in new regime AND confidence is high enough
            if self._bars_in_regime >= min_bars and confidence >= min_conf:
                self._current_regime = regime
                self._regime_switches += 1
                self._bars_in_regime = 0
        else:
            self._bars_in_regime = 0

        # Select strategy based on regime
        if regime == 'bull':
            # Bull market: trend-following — SuperTrend primary, TrendFollower fallback
            signal = self._run_sub_strategy(self._bull_supertrend, i, pos, "牛市")
            if signal.signal_type == SignalType.HOLD and pos == 0:
                signal = self._run_sub_strategy(self._bull_trend, i, pos, "牛市")
            # In bull, ignore SHORT signals
            if signal.signal_type == SignalType.SELL:
                return Signal(SignalType.HOLD, "", price,
                            reason=f"[牛市] 忽略做空信号")
            # Also allow exiting via CLOSE_LONG/CLOSE_SHORT from sub-strategies
            return signal

        elif regime == 'bear':
            # Bear market: Grid + RSI short bias
            signal = self._run_sub_strategy(self._bear_grid, i, pos, "熊市")
            if signal.signal_type == SignalType.HOLD and pos == 0:
                signal = self._run_sub_strategy(self._bear_rsi, i, pos, "熊市")
            # In bear, only allow SHORT entries, not BUY
            if signal.signal_type == SignalType.BUY:
                return Signal(SignalType.HOLD, "", price,
                            reason=f"[熊市] 忽略做多信号")
            return signal

        else:
            # Range market: RSI primary, BB fallback
            signal = self._run_sub_strategy(self._range_rsi, i, pos, "震荡")
            if signal.signal_type == SignalType.HOLD and pos == 0:
                signal = self._run_sub_strategy(self._range_bb, i, pos, "震荡")
            return signal
