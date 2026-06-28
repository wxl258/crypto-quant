# Strategy package — registers all built-in strategies on import
# All imports wrapped in try/except to survive Chaquopy environment
import logging
logger = logging.getLogger(__name__)

from strategy.base import StrategyRegistry

_strategies = [
    ("dual_ma", "strategy.dual_ma", "DualMAStrategy"),
    ("rsi_mean_reversion", "strategy.rsi_mean_reversion", "RSIMeanReversionStrategy"),
    ("grid", "strategy.grid", "GridStrategy"),
    ("bollinger_bands", "strategy.bollinger", "BollingerBandsStrategy"),
    ("macd", "strategy.macd", "MACDStrategy"),
    ("supertrend", "strategy.supertrend", "SuperTrendStrategy"),
    ("turtle", "strategy.turtle", "TurtleStrategy"),
    ("ensemble_conservative", "strategy.ensembles", "EnsembleConservative"),
    ("ensemble_balanced", "strategy.ensembles", "EnsembleBalanced"),
    ("ensemble_aggressive", "strategy.ensembles", "EnsembleAggressive"),
    ("ensemble_trend", "strategy.ensembles", "EnsembleTrend"),
    ("adaptive", "strategy.adaptive", "AdaptiveEnsembleStrategy"),
    ("trend_follower", "strategy.trend_follower", "TrendFollowerStrategy"),
    ("mean_reversion_v2", "strategy.mean_reversion_v2", "MeanReversionV2Strategy"),
    ("regime_adaptive", "strategy.regime_adaptive", "RegimeAdaptiveStrategy"),
    ("smart_meta", "strategy.smart_meta", "SmartMetaStrategy"),
    ("meta", "strategy.meta_strategy", "MetaStrategy"),
    ("ultimate", "strategy.ultimate", "UltimateStrategy"),
    ("smart_follower", "strategy.smart_follower", "SmartFollowerStrategy"),
    ("mtf", "strategy.mtf_strategy", "MTFStrategy"),
    ("funding_arb", "strategy.funding_arb", "FundingRateArbitrageStrategy"),
    ("portfolio", "strategy.portfolio", "PortfolioStrategy"),
    # AI strategies — may fail if scikit-learn not available
    ("ai_assisted", "strategy.ai_strategy", "AIAssistedStrategy"),
    ("multi_agent", "strategy.multi_agent_strategy", "MultiAgentStrategy"),
]

_import_errors = []

for _name, _module, _class in _strategies:
    try:
        mod = __import__(_module, fromlist=[_class])
        cls = getattr(mod, _class)
        StrategyRegistry.register(_name, cls)
    except Exception as e:
        _import_errors.append(f"{_name}: {e}")
        logger.warning(f"Strategy '{_name}' failed to load: {e}")

if _import_errors:
    logger.warning(f"{len(_import_errors)}/{len(_strategies)} strategies failed to load")
