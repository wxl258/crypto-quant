# Strategy package — registers all built-in strategies on import
from strategy.base import StrategyRegistry
from strategy.dual_ma import DualMAStrategy
from strategy.rsi_mean_reversion import RSIMeanReversionStrategy
from strategy.grid import GridStrategy
from strategy.bollinger import BollingerBandsStrategy
from strategy.macd import MACDStrategy
from strategy.supertrend import SuperTrendStrategy
from strategy.turtle import TurtleStrategy
from strategy.ensembles import (EnsembleConservative, EnsembleBalanced,
                                EnsembleAggressive, EnsembleTrend)
from strategy.adaptive import AdaptiveEnsembleStrategy
from strategy.trend_follower import TrendFollowerStrategy
from strategy.mean_reversion_v2 import MeanReversionV2Strategy
from strategy.regime_adaptive import RegimeAdaptiveStrategy
from strategy.smart_meta import SmartMetaStrategy
from strategy.meta_strategy import MetaStrategy
from strategy.ultimate import UltimateStrategy
from strategy.smart_follower import SmartFollowerStrategy
from strategy.mtf_strategy import MTFStrategy
from strategy.funding_arb import FundingRateArbitrageStrategy
from strategy.portfolio import PortfolioStrategy
from strategy.ai_strategy import AIAssistedStrategy
from strategy.multi_agent_strategy import MultiAgentStrategy

# Register all built-in strategies
StrategyRegistry.register("dual_ma", DualMAStrategy)
StrategyRegistry.register("rsi_mean_reversion", RSIMeanReversionStrategy)
StrategyRegistry.register("grid", GridStrategy)
StrategyRegistry.register("bollinger_bands", BollingerBandsStrategy)
StrategyRegistry.register("macd", MACDStrategy)
StrategyRegistry.register("supertrend", SuperTrendStrategy)
StrategyRegistry.register("turtle", TurtleStrategy)
StrategyRegistry.register("ensemble_conservative", EnsembleConservative)
StrategyRegistry.register("ensemble_balanced", EnsembleBalanced)
StrategyRegistry.register("ensemble_aggressive", EnsembleAggressive)
StrategyRegistry.register("ensemble_trend", EnsembleTrend)
StrategyRegistry.register("adaptive", AdaptiveEnsembleStrategy)
StrategyRegistry.register("trend_follower", TrendFollowerStrategy)
StrategyRegistry.register("mean_reversion_v2", MeanReversionV2Strategy)
StrategyRegistry.register("regime_adaptive", RegimeAdaptiveStrategy)
StrategyRegistry.register("smart_meta", SmartMetaStrategy)
StrategyRegistry.register("meta", MetaStrategy)
StrategyRegistry.register("ultimate", UltimateStrategy)
StrategyRegistry.register("smart_follower", SmartFollowerStrategy)
StrategyRegistry.register("mtf", MTFStrategy)
StrategyRegistry.register("funding_arb", FundingRateArbitrageStrategy)
StrategyRegistry.register("portfolio", PortfolioStrategy)
StrategyRegistry.register("ai_assisted", AIAssistedStrategy)
StrategyRegistry.register("multi_agent", MultiAgentStrategy)
