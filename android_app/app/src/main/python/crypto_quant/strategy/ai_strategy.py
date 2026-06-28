"""
AI-Assisted Trading Strategy

Combines ML trend prediction with technical indicators to generate
higher-confidence trading signals.

Uses GradientBoosting to predict trend direction, then confirms with
RSI, Bollinger Bands, and SMA50 before entering trades.
"""
from typing import Dict, List
import numpy as np
import os

from .base import Strategy, Signal, SignalType


class AIAssistedStrategy(Strategy):
    """Combines ML trend prediction with technical indicators.

    Uses GradientBoosting to predict trend direction, then confirms with
    technical indicators before entering trades.

    Entry logic:
    - Long:  ML predicts UP + RSI < 70 + price > SMA50
    - Short: ML predicts DOWN + RSI > 30 + price < SMA50

    Exit logic:
    - ML direction flips or confidence drops below 0.4
    """

    def _default_params(self):
        return {
            'model_path': '/workspace/crypto_quant/models/trend_predictor.pkl',
            'min_confidence': 0.6,
            'use_ml': True,
            'leverage': 3,
            'rsi_period': 14,
            'bb_period': 20,
            'bb_std': 2.0,
            'sma_period': 50,
        }

    def __init__(self, params: Dict = None, **kwargs):
        if params is None:
            params = {}
        if kwargs:
            params = {**params, **kwargs}
        super().__init__(params=params)
        self._predictor = None
        self._prev_direction = 0
        self._prev_confidence = 0.0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "model_path", "type": "str", "default": "/workspace/crypto_quant/models/trend_predictor.pkl", "label": "模型路径"},
            {"name": "min_confidence", "type": "float", "default": 0.6, "min": 0.3, "max": 0.95, "step": 0.05, "label": "最小置信度"},
            {"name": "use_ml", "type": "bool", "default": True, "label": "启用ML预测"},
            {"name": "leverage", "type": "int", "default": 3, "min": 1, "max": 10, "label": "杠杆倍数"},
            {"name": "rsi_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "RSI周期"},
            {"name": "bb_period", "type": "int", "default": 20, "min": 10, "max": 100, "label": "布林带周期"},
            {"name": "bb_std", "type": "float", "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1, "label": "布林带标准差"},
            {"name": "sma_period", "type": "int", "default": 50, "min": 20, "max": 200, "label": "SMA周期"},
        ]

    def init(self):
        """Initialize indicators and load ML model."""
        from ai.predictor import TrendPredictor

        # Load ML predictor
        model_path = self.get_param('model_path')
        use_ml = self.get_param('use_ml', True)

        if use_ml:
            self._predictor = TrendPredictor(model_path=model_path)

            # If model not trained yet, attempt to train on current data
            if not self._predictor.is_trained and self.data is not None and len(self.data) > 200:
                self._predictor.train(self.data, lookforward=4)

        # Compute technical indicators
        close = self.data['close'].values.astype(float)
        high = self.data['high'].values.astype(float)
        low = self.data['low'].values.astype(float)

        # RSI
        rsi_period = self.get_param('rsi_period', 14)
        self.add_indicator('rsi', self.rsi(close, rsi_period))

        # Bollinger Bands
        bb_period = self.get_param('bb_period', 20)
        bb_std = self.get_param('bb_std', 2.0)
        middle, upper, lower = self.bollinger_bands(close, bb_period, bb_std)
        self.add_indicator('bb_middle', middle)
        self.add_indicator('bb_upper', upper)
        self.add_indicator('bb_lower', lower)

        # SMA
        sma_period = self.get_param('sma_period', 50)
        self.add_indicator('sma', self.sma(close, sma_period))

        # Store previous prediction state
        self._prev_direction = 0
        self._prev_confidence = 0.0

    def _get_ml_prediction(self, i: int) -> dict:
        """Get ML prediction for the current bar.

        Uses data up to index i for prediction to avoid look-ahead bias.
        """
        use_ml = self.get_param('use_ml', True)

        if not use_ml or self._predictor is None or not self._predictor.is_trained:
            return {'direction': 0, 'confidence': 0.0, 'prob_up': 0.0, 'prob_down': 0.0}

        # Use data up to current bar (no future leak)
        df_subset = self.data.iloc[:i + 1]
        if len(df_subset) < 50:
            return {'direction': 0, 'confidence': 0.0, 'prob_up': 0.0, 'prob_down': 0.0}

        return self._predictor.predict(df_subset)

    def next(self, i: int) -> Signal:
        """Generate trading signal for bar i."""
        price = float(self.data['close'].iloc[i])
        pos = self.get_position()

        # Get indicators
        rsi_vals = self._indicators.get('rsi')
        sma_vals = self._indicators.get('sma')

        rsi = rsi_vals[i] if rsi_vals is not None and not np.isnan(rsi_vals[i]) else None
        sma = sma_vals[i] if sma_vals is not None and not np.isnan(sma_vals[i]) else None

        # Get ML prediction
        ml_pred = self._get_ml_prediction(i)
        ml_dir = ml_pred['direction']
        ml_conf = ml_pred['confidence']
        min_confidence = self.get_param('min_confidence', 0.6)

        # Track previous state for exit detection
        prev_dir = self._prev_direction
        prev_conf = self._prev_confidence

        # Update state
        self._prev_direction = ml_dir
        self._prev_confidence = ml_conf

        # --- Exit logic ---
        if pos != 0:
            # Exit if ML direction flips
            if ml_dir != 0 and ml_dir != pos:
                reason = (
                    f"ML方向反转 (置信度={ml_conf:.2f}, 方向={ml_dir})"
                )
                if pos == 1:
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=reason)
                else:
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=reason)

            # Exit if confidence drops too low
            if ml_conf < 0.4 and ml_conf > 0:
                reason = f"ML置信度下降 (置信度={ml_conf:.2f} < 0.4)"
                if pos == 1:
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=reason)
                else:
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=reason)

        # --- Entry logic ---
        if pos == 0:
            # Check ML confidence threshold
            if ml_dir == 0 or ml_conf < min_confidence:
                return Signal(SignalType.HOLD, "", price)

            # Check RSI and SMA availability
            if rsi is None or sma is None:
                return Signal(SignalType.HOLD, "", price)

            # Long signal: ML says UP AND RSI < 70 AND price > SMA50
            if ml_dir == 1:
                if rsi >= 70:
                    return Signal(SignalType.HOLD, "", price,
                                  reason=f"ML看多但RSI过热({rsi:.1f})")
                if price <= sma:
                    return Signal(SignalType.HOLD, "", price,
                                  reason=f"ML看多但价格低于SMA({sma:.2f})")

                return Signal(
                    SignalType.BUY, "", price,
                    reason=f"AI看多 (ML置信度={ml_conf:.2f}, RSI={rsi:.1f})",
                )

            # Short signal: ML says DOWN AND RSI > 30 AND price < SMA50
            elif ml_dir == -1:
                if rsi <= 30:
                    return Signal(SignalType.HOLD, "", price,
                                  reason=f"ML看空但RSI超卖({rsi:.1f})")
                if price >= sma:
                    return Signal(SignalType.HOLD, "", price,
                                  reason=f"ML看空但价格高于SMA({sma:.2f})")

                return Signal(
                    SignalType.SELL, "", price,
                    reason=f"AI看空 (ML置信度={ml_conf:.2f}, RSI={rsi:.1f})",
                )

        return Signal(SignalType.HOLD, "", price)
