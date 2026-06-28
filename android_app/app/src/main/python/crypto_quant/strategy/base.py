"""
Strategy Base Class and Registry
"""
import logging
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"


@dataclass
class Signal:
    signal_type: SignalType
    symbol: str
    price: float
    reason: str = ""
    timestamp: Any = None
    quantity: Optional[float] = None


class Strategy:
    """Base class for all trading strategies."""
    
    def __init__(self, params: Optional[Dict] = None, **kwargs):
        # 支持 Strategy(params_dict) 和 Strategy(param1=1, param2=2) 两种调用方式
        if params is None:
            params = {}
        if not isinstance(params, dict):
            params = {}
        # kwargs 也合并到 params
        if kwargs:
            params = {**params, **kwargs}
        self.symbol = params.pop("symbol", "")
        self.data = params.pop("data", None)
        self._params = params
        # 仓位追踪（用于回测引擎兼容）
        self._position = 0
        self._entry_price = 0.0
        self._entry_bar = -1
        self._indicators: Dict[str, Any] = {}
    
    def get_param(self, name: str, default=None):
        return self._params.get(name, default)
    
    @classmethod
    def get_param_info(cls) -> List[Dict[str, Any]]:
        return []
    
    def _default_params(self) -> Dict[str, Any]:
        return {}
    
    # ── 数据与指标方法 ──
    def set_data(self, data):
        """Set OHLCV data for the strategy."""
        self.data = data
    
    def add_indicator(self, name: str, value: Any):
        """Store precomputed indicator for access during next()."""
        self._indicators[name] = value
    
    def get_indicator(self, name: str, default=None):
        """Get precomputed indicator by name."""
        return self._indicators.get(name, default)
    
    # ── 生命周期方法 ──
    def init(self):
        """Initialize strategy - precompute indicators."""
        pass
    
    def next(self, i: int) -> Signal:
        """Called for each bar. Return a Signal."""
        return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)
    
    # ── 仓位管理（回测引擎兼容） ──
    def set_position(self, position: int, entry_price: float):
        """Track current position from backtest engine."""
        self._position = position
        self._entry_price = entry_price
    
    def get_position(self) -> int:
        """Return current position: 0=none, 1=long, -1=short."""
        return self._position
    
    def record_entry(self, i: int):
        """Record entry bar index."""
        self._entry_bar = i
    
    def record_exit(self, i: int):
        """Record exit bar index."""
        self._entry_bar = -1
        self._entry_price = 0.0
    
    def can_enter(self, i: int) -> bool:
        """Cooldown / entry permission check. Override for custom logic."""
        min_hold = self.get_param('min_hold_bars', 0)
        if min_hold and self._entry_bar >= 0:
            # Simple cooldown: wait min_hold bars after exit
            if i - self._entry_bar < min_hold:
                return False
        return True
    
    def get_volatility_adjusted_position_pct(self, i: int, base_pct: float) -> float:
        """Return position size fraction adjusted by volatility."""
        return base_pct
    
    # ── 技术指标工具 ──
    def sma(self, *args):
        """Simple Moving Average. Supports both sma(period) and sma(data, period)."""
        import numpy as np
        if len(args) == 1:
            period = args[0]
            closes = self.data['close'].values
        elif len(args) == 2:
            closes, period = args
        else:
            raise TypeError("sma() takes 1 or 2 arguments")
        result = np.zeros_like(closes)
        for i in range(len(closes)):
            if i >= period - 1:
                result[i] = np.mean(closes[i - period + 1:i + 1])
            else:
                result[i] = np.nan
        return result
    
    def ema(self, *args):
        """Exponential Moving Average. Supports both ema(period) and ema(data, period)."""
        import numpy as np
        if len(args) == 1:
            period = args[0]
            closes = self.data['close'].values
        elif len(args) == 2:
            closes, period = args
        else:
            raise TypeError("ema() takes 1 or 2 arguments")
        result = np.zeros_like(closes)
        multiplier = 2 / (period + 1)
        result[0] = closes[0]
        for i in range(1, len(closes)):
            result[i] = (closes[i] - result[i-1]) * multiplier + result[i-1]
        return result
    
    def atr(self, *args):
        """Average True Range. Supports both atr(period) and atr(high, low, close, period)."""
        import numpy as np
        if len(args) == 1:
            period = args[0]
            high = self.data['high'].values
            low = self.data['low'].values
            close = self.data['close'].values
        elif len(args) == 4:
            high, low, close, period = args
        else:
            raise TypeError("atr() takes 1 or 4 arguments")
        tr = np.maximum(high - low,
                       np.maximum(np.abs(high - np.roll(close, 1)),
                                  np.abs(low - np.roll(close, 1))))
        tr[0] = high[0] - low[0]
        result = np.zeros_like(close)
        result[:period] = np.mean(tr[:period])
        for i in range(period, len(close)):
            result[i] = (result[i-1] * (period - 1) + tr[i]) / period
        return result
    
    def rsi(self, *args):
        """Relative Strength Index. Supports both rsi(period) and rsi(data, period)."""
        import numpy as np
        if len(args) == 1:
            period = args[0]
            closes = self.data['close'].values
        elif len(args) == 2:
            closes, period = args
        else:
            raise TypeError("rsi() takes 1 or 2 arguments")
        
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
    
    def signal_quality_score(self, i: int, direction: str) -> float:
        """Default signal quality score. Override in subclasses for advanced filtering."""
        return 1.0
    
    def is_trending_adx(self, high, low, close, i: int, period: int = 14, threshold: float = 25.0) -> bool:
        """Default ADX trend check. Returns False (no strong trend) by default."""
        return False
    
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
    
    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9):
        """MACD, signal line, histogram"""
        import numpy as np
        fast_ema = self.ema(fast)
        slow_ema = self.ema(slow)
        macd_line = fast_ema - slow_ema
        signal_line = np.zeros_like(macd_line)
        mult = 2 / (signal + 1)
        signal_line[0] = macd_line[0]
        for i in range(1, len(macd_line)):
            signal_line[i] = (macd_line[i] - signal_line[i-1]) * mult + signal_line[i-1]
        return macd_line, signal_line, macd_line - signal_line


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
        """Return registered strategies with full info."""
        result = []
        for name, strategy_cls in cls._strategies.items():
            # Get docstring (first line as short description)
            doc = (strategy_cls.__doc__ or "").strip()
            short_desc = doc.split('\n')[0].strip() if doc else ""
            # Get params
            params = []
            if hasattr(strategy_cls, 'get_param_info'):
                try:
                    params = strategy_cls.get_param_info()
                except Exception:
                    pass
            # If no params from get_param_info, try _default_params
            if not params and hasattr(strategy_cls, '_default_params'):
                try:
                    inst = strategy_cls()
                    default_params = inst._default_params() if hasattr(inst, '_default_params') else {}
                    for k, v in default_params.items():
                        params.append({"name": k, "label": k, "default": v, "type": type(v).__name__})
                except Exception:
                    pass

            result.append({
                "name": name,
                "class": strategy_cls.__name__,
                "module": strategy_cls.__module__,
                "description": short_desc,
                "parameters": params,
            })
        return result
    
    @classmethod
    def get_param_info(cls, name: str) -> List[Dict]:
        strategy_cls = cls.get(name)
        if strategy_cls and hasattr(strategy_cls, 'get_param_info'):
            return strategy_cls.get_param_info()
        return []
