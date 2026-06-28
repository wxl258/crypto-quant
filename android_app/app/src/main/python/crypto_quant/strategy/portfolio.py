"""
Portfolio Strategy Manager — Multi-strategy ensemble with signal voting and dynamic weights.
Runs multiple sub-strategies simultaneously and combines their signals.
"""
from typing import Dict, List, Tuple, Optional
import numpy as np
from .base import Strategy, Signal, SignalType


class PortfolioStrategy(Strategy):
    """Runs multiple strategies in parallel and combines signals via voting.
    
    Modes:
      - majority: Requires N+ strategies to agree (simple majority)
      - unanimous: All strategies must agree
      - weighted: Each strategy has a weight, weighted sum determines direction
    
    v2.0: Dynamic weights — sub-strategy weights auto-adjust based on recent
    trade performance. Losing strategies are down-weighted, winners up-weighted.
    """

    def _default_params(self):
        return {
            'strategies': [],
            'vote_mode': 'majority',
            'vote_threshold': 0.5,
            'require_exit_consensus': False,
            'use_dynamic_weights': True,
            'weight_lookback': 20,
        }

    def __init__(self, params: Dict = None, **kwargs):
        if params is None:
            params = {}
        if kwargs:
            params = {**params, **kwargs}
        super().__init__(params=params)
        self._sub_strategies: List[Tuple[Strategy, float]] = []
        self._base_weights: List[float] = []
        self._trade_pnls: List[List[float]] = []  # per-strategy recent PnLs

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "vote_mode", "type": "str", "default": "majority", "label": "投票模式(majority/unanimous/weighted)"},
            {"name": "vote_threshold", "type": "float", "default": 0.5, "min": 0.3, "max": 1.0, "step": 0.1, "label": "加权阈值"},
            {"name": "require_exit_consensus", "type": "bool", "default": False, "label": "平仓需共识"},
            {"name": "use_dynamic_weights", "type": "bool", "default": True, "label": "动态权重调整"},
            {"name": "weight_lookback", "type": "int", "default": 20, "min": 5, "max": 50, "label": "权重回溯交易数"},
        ]

    def add_strategy(self, strategy: Strategy, weight: float = 1.0):
        """Add a sub-strategy with given voting weight."""
        self._sub_strategies.append((strategy, weight))
        self._base_weights.append(weight)
        self._trade_pnls.append([])

    def _get_dynamic_weight(self, idx: int) -> float:
        """Calculate dynamic weight based on recent trade performance."""
        if not self.get_param('use_dynamic_weights', True):
            return self._base_weights[idx]
        
        lookback = self.get_param('weight_lookback', 20)
        pnls = self._trade_pnls[idx][-lookback:]
        
        if len(pnls) < 3:
            return self._base_weights[idx]
        
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls)
        # Weight adjustment: 0.5x ~ 1.5x based on win rate
        # < 30% win → 0.5x, 50% → 1.0x, > 70% → 1.5x
        factor = 0.5 + win_rate  # 30% → 0.8, 50% → 1.0, 70% → 1.2
        factor = max(0.3, min(factor, 1.5))
        
        return self._base_weights[idx] * factor

    def record_trade_result(self, idx: int, pnl: float):
        """Record a trade result for dynamic weight calculation."""
        while len(self._trade_pnls) <= idx:
            self._trade_pnls.append([])
        self._trade_pnls[idx].append(pnl)
        # Keep only recent history
        max_keep = self.get_param('weight_lookback', 20) * 3
        if len(self._trade_pnls[idx]) > max_keep:
            self._trade_pnls[idx] = self._trade_pnls[idx][-max_keep:]

    def set_data(self, data):
        super().set_data(data)
        for s, _ in self._sub_strategies:
            s.set_data(data)

    def init(self):
        for s, _ in self._sub_strategies:
            s.init()
        self._vote_mode = self.get_param('vote_mode', 'majority')
        self._vote_threshold = self.get_param('vote_threshold', 0.5)

    def _collect_votes(self, i: int) -> Dict[str, float]:
        """Collect BUY/SELL/HOLD votes from all sub-strategies with dynamic weights."""
        pos = self.get_position()
        votes = {'BUY': 0.0, 'SELL': 0.0, 'HOLD': 0.0,
                 'CLOSE_LONG': 0.0, 'CLOSE_SHORT': 0.0}
        for idx, (strat, _) in enumerate(self._sub_strategies):
            weight = self._get_dynamic_weight(idx)
            strat._position = pos
            signal = strat.next(i)
            st = signal.signal_type.value
            
            if pos == 0:
                if st == 'BUY':
                    votes['BUY'] += weight
                elif st == 'SELL':
                    votes['SELL'] += weight
                else:
                    votes['HOLD'] += weight
            elif pos == 1:
                if st in ('CLOSE_LONG', 'SELL'):
                    votes['CLOSE_LONG'] += weight
                elif st == 'BUY':
                    votes['HOLD'] += weight
                else:
                    votes['HOLD'] += weight
            elif pos == -1:
                if st in ('CLOSE_SHORT', 'BUY'):
                    votes['CLOSE_SHORT'] += weight
                elif st == 'SELL':
                    votes['HOLD'] += weight
                else:
                    votes['HOLD'] += weight
        return votes

    def _decide(self, votes: Dict[str, float], pos: int) -> Tuple[Optional[str], float]:
        """Decide final signal from collected votes."""
        total_weight = sum(self._get_dynamic_weight(i) for i in range(len(self._sub_strategies)))
        if total_weight == 0:
            return None, 0

        mode = self._vote_mode
        threshold = self._vote_threshold

        if mode == 'unanimous':
            if votes['BUY'] >= total_weight * 0.99:
                return 'BUY', votes['BUY'] / total_weight
            if votes['SELL'] >= total_weight * 0.99:
                return 'SELL', votes['SELL'] / total_weight
            if pos != 0 and votes.get(f'CLOSE_{"LONG" if pos==1 else "SHORT"}', 0) >= total_weight * 0.99:
                return f'CLOSE_{"LONG" if pos==1 else "SHORT"}', 1.0
            return None, 0

        elif mode == 'weighted':
            buy_ratio = votes['BUY'] / total_weight
            sell_ratio = votes['SELL'] / total_weight
            close_long_ratio = votes['CLOSE_LONG'] / total_weight
            close_short_ratio = votes['CLOSE_SHORT'] / total_weight

            if pos == 0:
                if buy_ratio >= sell_ratio and buy_ratio >= threshold:
                    return 'BUY', buy_ratio
                if sell_ratio > buy_ratio and sell_ratio >= threshold:
                    return 'SELL', sell_ratio
            elif pos == 1:
                if close_long_ratio >= threshold:
                    return 'CLOSE_LONG', close_long_ratio
            elif pos == -1:
                if close_short_ratio >= threshold:
                    return 'CLOSE_SHORT', close_short_ratio
            return None, 0

        else:  # majority
            total_weight = sum(self._get_dynamic_weight(i) for i in range(len(self._sub_strategies)))
            half = total_weight / 2
            if pos == 0:
                if votes['BUY'] > half:
                    return 'BUY', votes['BUY'] / total_weight
                if votes['SELL'] > half:
                    return 'SELL', votes['SELL'] / total_weight
            elif pos == 1 and votes['CLOSE_LONG'] > half:
                return 'CLOSE_LONG', votes['CLOSE_LONG'] / total_weight
            elif pos == -1 and votes['CLOSE_SHORT'] > half:
                return 'CLOSE_SHORT', votes['CLOSE_SHORT'] / total_weight
            return None, 0

    def next(self, i: int) -> Signal:
        votes = self._collect_votes(i)
        pos = self.get_position()
        require_exit = self.get_param('require_exit_consensus', False)

        decision, confidence = self._decide(votes, pos)

        price = self.data['close'].iloc[i]

        if decision is None:
            return Signal(SignalType.HOLD, "", price)

        if decision == 'BUY' and pos == 0:
            return Signal(SignalType.BUY, "", price,
                         reason=f"组合投票做多(置信度{confidence:.0%})")
        elif decision == 'SELL' and pos == 0:
            return Signal(SignalType.SELL, "", price,
                         reason=f"组合投票做空(置信度{confidence:.0%})")
        elif decision == 'CLOSE_LONG' and pos == 1:
            # require_exit_consensus: only exit if enough votes
            if require_exit and confidence < 0.5:
                return Signal(SignalType.HOLD, "", price,
                             reason=f"平多未达共识(置信度{confidence:.0%}<50%)")
            return Signal(SignalType.CLOSE_LONG, "", price,
                         reason=f"组合投票平多(置信度{confidence:.0%})")
        elif decision == 'CLOSE_SHORT' and pos == -1:
            if require_exit and confidence < 0.5:
                return Signal(SignalType.HOLD, "", price,
                             reason=f"平空未达共识(置信度{confidence:.0%}<50%)")
            return Signal(SignalType.CLOSE_SHORT, "", price,
                         reason=f"组合投票平空(置信度{confidence:.0%})")

        return Signal(SignalType.HOLD, "", price)
