"""
Strategy Base Class and Registry
"""
import logging
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    signal_type: SignalType
    symbol: str
    price: float
    reason: str = ""


class Strategy:
    """Base class for all trading strategies."""
    
    def __init__(self, **kwargs):
        self.symbol = kwargs.get("symbol", "")
        self.data = kwargs.get("data", None)
        self._params = kwargs.get("params", {})
        for k, v in kwargs.items():
            if k not in ("symbol", "data", "params"):
                self._params[k] = v
    
    def get_param(self, name: str, default=None):
        return self._params.get(name, default)
    
    @classmethod
    def get_param_info(cls) -> List[Dict[str, Any]]:
        return []
    
    def sma(self, period: int):
        """Simple Moving Average"""
        import numpy as np
        closes = self.data['close'].values
        result = np.zeros_like(closes)
        for i in range(len(closes)):
            if i >= period - 1:
                result[i] = np.mean(closes[i - period + 1:i + 1])
            else:
                result[i] = np.nan
        return result
    
    def ema(self, period: int):
        """Exponential Moving Average"""
        import numpy as np
        closes = self.data['close'].values
        result = np.zeros_like(closes)
        multiplier = 2 / (period + 1)
        result[0] = closes[0]
        for i in range(1, len(closes)):
            result[i] = (closes[i] - result[i-1]) * multiplier + result[i-1]
        return result
    
    def rsi(self, period: int = 14):
        """Relative Strength Index"""
        import numpy as np
        closes = self.data['close'].values
        deltas = np.diff(closes, prepend=closes[0])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        result = np.zeros_like(closes)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        for i in range(period, len(closes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                result[i] = 100
            else:
                rs = avg_gain / avg_loss
                result[i] = 100 - (100 / (1 + rs))
        
        return result
    
    def bollinger_bands(self, period: int = 20, std_dev: float = 2.0):
        """Bollinger Bands"""
        import numpy as np
        closes = self.data['close'].values
        mid = np.zeros_like(closes)
        upper = np.zeros_like(closes)
        lower = np.zeros_like(closes)
        
        for i in range(len(closes)):
            if i >= period - 1:
                window = closes[i - period + 1:i + 1]
                mid[i] = np.mean(window)
                std = np.std(window)
                upper[i] = mid[i] + std_dev * std
                lower[i] = mid[i] - std_dev * std
            else:
                mid[i] = np.nan
                upper[i] = np.nan
                lower[i] = np.nan
        
        return upper, mid, lower
    
    def init(self):
        """Initialize strategy - precompute indicators."""
        pass
    
    def next(self, i: int):
        """Called for each bar. Return a Signal."""
        return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)


class StrategyRegistry:
    """Registry for all strategies."""
    
    _strategies: Dict[str, type] = {}
    
    @classmethod
    def register(cls, name: str, strategy_class: type):
        cls._strategies[name] = strategy_class
        logger.debug(f"Registered strategy: {name}")
    
    @classmethod
    def get(cls, name: str) -> Optional[type]:
        return cls._strategies.get(name)
    
    @classmethod
    def list(cls) -> Dict[str, type]:
        return dict(cls._strategies)
    
    @classmethod
    def list_strategies(cls) -> List[Dict]:
        """Return registered strategies with name and class info."""
        result = []
        for name, strategy_cls in cls._strategies.items():
            result.append({
                "name": name,
                "class": strategy_cls.__name__,
                "module": strategy_cls.__module__,
            })
        return result

    @classmethod
    def get_param_info(cls, name: str) -> List[Dict]:
        strategy_cls = cls.get(name)
        if strategy_cls and hasattr(strategy_cls, 'get_param_info'):
            return strategy_cls.get_param_info()
        return []
