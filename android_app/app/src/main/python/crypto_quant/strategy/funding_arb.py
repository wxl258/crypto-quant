"""
FundingRateArbitrage — Exploit perpetual futures funding rate mechanism.

When funding rate is positive (>0.01%): longs pay shorts → SHORT to collect funding
When funding rate is negative (<-0.01%): shorts pay longs → LONG to collect funding

This strategy opens a position when funding rate is extreme and holds until
the rate normalizes or the position reaches take-profit.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType


class FundingRateArbitrageStrategy(Strategy):
    """Funding rate arbitrage using extreme funding rate signals."""

    def _default_params(self):
        return {
            'funding_long_threshold': -0.0003,   # -0.03% → go long
            'funding_short_threshold': 0.0003,   # +0.03% → go short
            'exit_threshold': 0.00005,           # ±0.005% → exit
            'min_hold_hours': 8,                 # minimum hold time
            'max_hold_hours': 72,                # maximum hold time
            'tp_pct': 0.02,                      # 2% take profit
            'sl_pct': 0.03,                      # 3% stop loss
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self._entry_bar = -1
        self._entry_price = 0.0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "funding_long_threshold", "type": "float", "default": -0.0003, "min": -0.001, "max": -0.0001, "step": 0.0001, "label": "做多费率阈值"},
            {"name": "funding_short_threshold", "type": "float", "default": 0.0003, "min": 0.0001, "max": 0.001, "step": 0.0001, "label": "做空费率阈值"},
            {"name": "exit_threshold", "type": "float", "default": 0.00005, "min": 0.00001, "max": 0.0002, "step": 0.00001, "label": "退出阈值"},
            {"name": "min_hold_hours", "type": "int", "default": 8, "min": 4, "max": 24, "label": "最小持仓小时"},
            {"name": "max_hold_hours", "type": "int", "default": 72, "min": 24, "max": 168, "label": "最大持仓小时"},
            {"name": "tp_pct", "type": "float", "default": 0.02, "min": 0.01, "max": 0.05, "step": 0.005, "label": "止盈比例"},
            {"name": "sl_pct", "type": "float", "default": 0.03, "min": 0.01, "max": 0.05, "step": 0.005, "label": "止损比例"},
        ]

    def init(self):
        close = self.data['close'].values
        self.add_indicator('sma20', self.sma(close, 20))

    def _estimate_funding_rate(self, i: int) -> float:
        """Estimate funding rate from price premium over spot (proxy).
        In production, use actual exchange funding rate API.
        Here we use: (price - SMA20) / SMA20 as a rough proxy."""
        sma = self._indicators.get('sma20')
        price = self.data['close'].iloc[i]
        if sma is None or np.isnan(sma[i]) or sma[i] <= 0:
            return 0.0
        # Premium over SMA = positive funding rate proxy
        return (price - sma[i]) / sma[i]

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        pos = self.get_position()
        
        funding = self._estimate_funding_rate(i)
        bars_held = i - self._entry_bar if self._entry_bar >= 0 else 0

        # Exit conditions
        if pos != 0:
            # Take profit / Stop loss
            if pos == 1:
                if price >= self._entry_price * (1 + self.get_param('tp_pct', 0.02)):
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=f"费率套利止盈 +{self.get_param('tp_pct',0.02)*100:.0f}%")
                if price <= self._entry_price * (1 - self.get_param('sl_pct', 0.03)):
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=f"费率套利止损 -{self.get_param('sl_pct',0.03)*100:.0f}%")
            else:
                if price <= self._entry_price * (1 - self.get_param('tp_pct', 0.02)):
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=f"费率套利止盈 +{self.get_param('tp_pct',0.02)*100:.0f}%")
                if price >= self._entry_price * (1 + self.get_param('sl_pct', 0.03)):
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=f"费率套利止损 -{self.get_param('sl_pct',0.03)*100:.0f}%")
            
            # Exit when funding normalizes
            if abs(funding) < self.get_param('exit_threshold', 0.00005):
                if pos == 1:
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=f"费率回归正常({funding*100:.3f}%)")
                else:
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=f"费率回归正常({funding*100:.3f}%)")
            
            # Max hold time
            max_bars = self.get_param('max_hold_hours', 72)
            if bars_held >= max_bars:
                if pos == 1:
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_LONG, "", price, reason=f"最大持仓{max_bars}小时到期")
                else:
                    self._entry_bar = -1
                    return Signal(SignalType.CLOSE_SHORT, "", price, reason=f"最大持仓{max_bars}小时到期")
            
            return Signal(SignalType.HOLD, "", price)

        # Entry conditions
        min_bars = self.get_param('min_hold_hours', 8)
        if i < min_bars:
            return Signal(SignalType.HOLD, "", price)

        long_threshold = self.get_param('funding_long_threshold', -0.0003)
        short_threshold = self.get_param('funding_short_threshold', 0.0003)

        if funding <= long_threshold:
            self._entry_bar = i
            self._entry_price = price
            return Signal(SignalType.BUY, "", price, reason=f"负费率套利({funding*100:.3f}%)做多")

        if funding >= short_threshold:
            self._entry_bar = i
            self._entry_price = price
            return Signal(SignalType.SELL, "", price, reason=f"正费率套利({funding*100:.3f}%)做空")

        return Signal(SignalType.HOLD, "", price)
