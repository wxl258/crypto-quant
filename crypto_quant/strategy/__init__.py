# Strategy package — registers all built-in strategies via lazy loading
from strategy.base import StrategyRegistry

# Lazy module mapping — strategies are imported on first access.
# Each entry maps a strategy name to (module_path, class_name).
_LAZY_MODULES = {
    "dual_ma": ("strategy.dual_ma", "DualMAStrategy"),
    "rsi_mean_reversion": ("strategy.rsi_mean_reversion", "RSIMeanReversionStrategy"),
    "grid": ("strategy.grid", "GridStrategy"),
    "bollinger_bands": ("strategy.bollinger", "BollingerBandsStrategy"),
    "macd": ("strategy.macd", "MACDStrategy"),
    "supertrend": ("strategy.supertrend", "SuperTrendStrategy"),
    "turtle": ("strategy.turtle", "TurtleStrategy"),
    "ensemble_conservative": ("strategy.ensembles", "EnsembleConservative"),
    "ensemble_balanced": ("strategy.ensembles", "EnsembleBalanced"),
    "ensemble_aggressive": ("strategy.ensembles", "EnsembleAggressive"),
    "ensemble_trend": ("strategy.ensembles", "EnsembleTrend"),
    "adaptive": ("strategy.adaptive", "AdaptiveEnsembleStrategy"),
    "trend_follower": ("strategy.trend_follower", "TrendFollowerStrategy"),
    "mean_reversion_v2": ("strategy.mean_reversion_v2", "MeanReversionV2Strategy"),
    "regime_adaptive": ("strategy.regime_adaptive", "RegimeAdaptiveStrategy"),
    "smart_meta": ("strategy.smart_meta", "SmartMetaStrategy"),
    "meta": ("strategy.meta_strategy", "MetaStrategy"),
    "ultimate": ("strategy.ultimate", "UltimateStrategy"),
    "smart_follower": ("strategy.smart_follower", "SmartFollowerStrategy"),
    "mtf": ("strategy.mtf_strategy", "MTFStrategy"),
    "funding_arb": ("strategy.funding_arb", "FundingRateArbitrageStrategy"),
    "portfolio": ("strategy.portfolio", "PortfolioStrategy"),
    "ai_assisted": ("strategy.ai_strategy", "AIAssistedStrategy"),
    "multi_agent": ("strategy.multi_agent_strategy", "MultiAgentStrategy"),
}

for name, (module_path, class_name) in _LAZY_MODULES.items():
    StrategyRegistry.register_lazy(name, module_path, class_name)
