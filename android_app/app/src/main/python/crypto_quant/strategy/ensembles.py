"""
Pre-built Strategy Ensembles — ready-to-use portfolio combinations.

Each ensemble combines 3-4 strategies with different voting modes.
"""
from typing import Dict, List
from .base import Strategy, Signal, SignalType, StrategyRegistry
from .portfolio import PortfolioStrategy
from .bollinger import BollingerBandsStrategy
from .rsi_mean_reversion import RSIMeanReversionStrategy
from .macd import MACDStrategy
from .supertrend import SuperTrendStrategy
from .turtle import TurtleStrategy
from .dual_ma import DualMAStrategy


class EnsembleConservative(PortfolioStrategy):
    """保守组合: RSI均值回归 + 布林带回归 — 加权投票，任一策略即可入场
    
    两个最强均值回归策略，RSI和布林带各自独立产生信号，降低阈值让任一策略可触发。
    """
    def _default_params(self):
        return {
            'vote_mode': 'weighted',
            'vote_threshold': 0.3,
            'require_exit_consensus': False,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self.add_strategy(RSIMeanReversionStrategy({'rsi_period': 14, 'oversold': 30, 'overbought': 70, 'exit_mid': 50, 'use_mid_exit': True}), 1.0)
        self.add_strategy(BollingerBandsStrategy({'period': 20, 'std_dev': 2.0, 'use_reversal': True}), 1.0)

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "vote_mode", "type": "str", "default": "weighted", "label": "投票模式"},
            {"name": "vote_threshold", "type": "float", "default": 0.3, "min": 0.2, "max": 0.8, "step": 0.05, "label": "投票阈值"},
            {"name": "require_exit_consensus", "type": "bool", "default": False, "label": "平仓需共识"},
        ]


class EnsembleBalanced(PortfolioStrategy):
    """均衡组合: RSI + 布林带 + MACD — 加权投票
    
    均值回归 + 趋势确认。RSI和布林带提供主要信号，MACD权重减半辅助确认。
    """
    def _default_params(self):
        return {
            'vote_mode': 'weighted',
            'vote_threshold': 0.3,
            'require_exit_consensus': False,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self.add_strategy(RSIMeanReversionStrategy({'rsi_period': 14, 'oversold': 30, 'overbought': 70}), 1.0)
        self.add_strategy(BollingerBandsStrategy({'period': 20, 'std_dev': 2.0, 'use_reversal': True}), 1.0)
        self.add_strategy(MACDStrategy({'fast_period': 12, 'slow_period': 26, 'signal_period': 9}), 0.5)

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "vote_mode", "type": "str", "default": "weighted", "label": "投票模式"},
            {"name": "vote_threshold", "type": "float", "default": 0.3, "min": 0.2, "max": 0.8, "step": 0.05, "label": "投票阈值"},
            {"name": "require_exit_consensus", "type": "bool", "default": False, "label": "平仓需共识"},
        ]


class EnsembleAggressive(PortfolioStrategy):
    """激进组合: RSI + 布林带 + MACD + 双均线 — 加权投票，低阈值
    
    四个策略独立产生信号，任意一个即可触发。
    """
    def _default_params(self):
        return {
            'vote_mode': 'weighted',
            'vote_threshold': 0.25,
            'require_exit_consensus': False,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self.add_strategy(RSIMeanReversionStrategy({'rsi_period': 14, 'oversold': 30, 'overbought': 70}), 1.0)
        self.add_strategy(BollingerBandsStrategy({'period': 20, 'std_dev': 2.0, 'use_reversal': True}), 1.0)
        self.add_strategy(MACDStrategy({'fast_period': 12, 'slow_period': 26, 'signal_period': 9}), 1.0)
        self.add_strategy(DualMAStrategy({'fast_period': 10, 'slow_period': 30, 'use_ema': True}), 1.0)

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "vote_mode", "type": "str", "default": "weighted", "label": "投票模式"},
            {"name": "vote_threshold", "type": "float", "default": 0.25, "min": 0.15, "max": 0.6, "step": 0.05, "label": "投票阈值"},
            {"name": "require_exit_consensus", "type": "bool", "default": False, "label": "平仓需共识"},
        ]


class EnsembleTrend(PortfolioStrategy):
    """趋势组合: 超级趋势 + 海龟 + MACD — 三个趋势策略确认方向
    
    纯趋势跟踪，适合单边行情。
    """
    def _default_params(self):
        return {
            'vote_mode': 'majority',
            'vote_threshold': 0.5,
            'require_exit_consensus': True,
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self.add_strategy(SuperTrendStrategy({'fast_atr': 10, 'fast_mult': 2.0, 'slow_atr': 14, 'slow_mult': 3.0, 'cooldown_bars': 3}), 1.0)
        self.add_strategy(TurtleStrategy({'entry_period': 20, 'exit_period': 10, 'atr_period': 20, 'atr_stop': 2.0}), 1.0)
        self.add_strategy(MACDStrategy({'fast_period': 12, 'slow_period': 26, 'signal_period': 9}), 1.0)

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "vote_mode", "type": "str", "default": "majority", "label": "投票模式"},
            {"name": "require_exit_consensus", "type": "bool", "default": True, "label": "平仓需共识"},
        ]
