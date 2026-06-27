"""
SmartFollower — Cycle-aware hybrid of trend_follower + smart_meta.

Key insight from 2000+ backtests:
- trend_follower on 1d: +693% BTC 30d, +1147% ETH 90d (BEST performer)
- trend_follower on 1h/4h: consistently loses money (high fee, noise)
- smart_meta on 1d/4h/1h: more balanced, best on SOL/DOGE/BNB

Solution: Use trend_follower on daily, smart_meta on intraday.
This eliminates trend_follower's intraday losses while keeping its daily edge.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType
from .trend_follower import TrendFollowerStrategy
from .smart_meta import SmartMetaStrategy


class SmartFollowerStrategy(Strategy):
    """trend_follower on 1d, smart_meta on 1h/4h. Best of both worlds."""

    def _default_params(self):
        return {}

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self._trend_follower = TrendFollowerStrategy()
        self._smart_meta = SmartMetaStrategy()

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return []

    def set_data(self, data):
        super().set_data(data)
        self._trend_follower.set_data(data)
        self._smart_meta.set_data(data)

    def init(self):
        self._trend_follower.init()
        self._smart_meta.init()

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        pos = self.get_position()

        # Detect interval from data frequency
        if len(self.data) >= 2:
            delta = self.data.index[1] - self.data.index[0]
            hours = delta.total_seconds() / 3600
        else:
            hours = 1

        # Daily or longer → trend_follower (proven best on daily)
        if hours >= 24:
            self._trend_follower._position = pos
            signal = self._trend_follower.next(i)
            signal.reason = f"[日线TF] {signal.reason}"
            return signal
        else:
            # Intraday → smart_meta (more adaptive, avoids trend_follower's intraday losses)
            self._smart_meta._position = pos
            signal = self._smart_meta.next(i)
            signal.reason = f"[短线SM] {signal.reason}"
            return signal
