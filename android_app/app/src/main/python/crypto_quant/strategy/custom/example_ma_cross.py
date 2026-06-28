"""
示例：增强版双均线策略 — 带成交量过滤
这是一个演示自定义策略开发的模板
"""
from strategy.base import Strategy, Signal, SignalType
import numpy as np


class EnhancedMACrossStrategy(Strategy):
    """增强版双均线策略：快慢线交叉 + 成交量确认"""
    
    @classmethod
    def get_param_info(cls):
        return [
            {"name": "fast_period", "type": "int", "default": 10, "description": "快线周期"},
            {"name": "slow_period", "type": "int", "default": 30, "description": "慢线周期"},
            {"name": "vol_threshold", "type": "float", "default": 1.2, "description": "成交量放大倍数"},
        ]
    
    def init(self):
        fast_p = self.get_param("fast_period", 10)
        slow_p = self.get_param("slow_period", 30)
        vol_t = self.get_param("vol_threshold", 1.2)
        
        self.fast_ma = self.sma(fast_p)
        self.slow_ma = self.sma(slow_p)
        self.vol_ma = self.sma(20)  # 这里sma默认用close，需要自己算vol的ma
        
        # 成交量均线（手动计算）
        closes = self.data['close'].values
        self.vol_avg = np.zeros_like(closes)
        for i in range(len(closes)):
            if i >= 19:
                self.vol_avg[i] = np.mean(self.data['volume'].values[i-19:i+1])
    
    def next(self, i: int):
        if i < 50:
            return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)
        
        price = self.data['close'][i]
        volume = self.data['volume'][i]
        
        fast = self.fast_ma[i]
        slow = self.slow_ma[i]
        prev_fast = self.fast_ma[i-1]
        prev_slow = self.slow_ma[i-1]
        
        # 金叉 + 放量 → 买入
        if prev_fast <= prev_slow and fast > slow:
            if volume > self.vol_avg[i] * self.get_param("vol_threshold", 1.2):
                return Signal(
                    signal_type=SignalType.BUY,
                    symbol=self.symbol,
                    price=price,
                    reason="金叉放量突破"
                )
        
        # 死叉 → 卖出
        if prev_fast >= prev_slow and fast < slow:
            return Signal(
                signal_type=SignalType.SELL,
                symbol=self.symbol,
                price=price,
                reason="死叉信号"
            )
        
        return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)
