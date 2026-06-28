"""信号过滤框架"""
import logging
from datetime import datetime, time
logger = logging.getLogger(__name__)

class SignalFilter:
    """信号过滤器基类"""
    def filter(self, signal, context=None):
        return True  # 默认通过

class TimeFilter(SignalFilter):
    """时间过滤：避开低流动性时段"""
    AVOID_HOURS = [0, 1]  # UTC 0:00-1:59
    
    def filter(self, signal, context=None):
        now = datetime.now()
        if now.hour in self.AVOID_HOURS:
            logger.info(f"Signal filtered by time: {now.hour}:00 UTC")
            return False
        return True

class SpreadFilter(SignalFilter):
    """价差过滤：bid-ask spread过大时拒绝"""
    def __init__(self, max_spread_pct=0.005):
        self.max_spread = max_spread_pct
    
    def filter(self, signal, context=None):
        if context and 'bid' in context and 'ask' in context:
            spread = (context['ask'] - context['bid']) / context['bid']
            if spread > self.max_spread:
                logger.info(f"Signal filtered by spread: {spread:.4f}")
                return False
        return True

class CorrelationFilter(SignalFilter):
    """相关性过滤：已有高度相关持仓时拒绝新开仓"""
    def __init__(self, max_correlation=0.85):
        self.max_corr = max_correlation
    
    def filter(self, signal, context=None):
        # 简化版：检查是否已有同方向持仓
        if context and 'positions' in context:
            for sym, pos in context['positions'].items():
                if pos.get('side') == ('LONG' if hasattr(signal, 'signal_type') and 'BUY' in str(signal.signal_type) else 'SHORT'):
                    return False
        return True

class FilterChain:
    """过滤器链"""
    def __init__(self):
        self._filters = []
    
    def add(self, f: SignalFilter):
        self._filters.append(f)
        return self
    
    def apply(self, signal, context=None):
        for f in self._filters:
            if not f.filter(signal, context):
                return False
        return True
