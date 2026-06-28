# Strategy package — lazy-loads built-in strategies on demand
import logging
logger = logging.getLogger(__name__)

from strategy.base import StrategyRegistry

# Lazy-load registry: maps strategy name → (module_path, class_name)
_lazy_strategies = [
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

# Store lazy-load info as a dict for fast lookup
_lazy_map = {name: (module, class_name) for name, module, class_name in _lazy_strategies}

# Monkey-patch StrategyRegistry.get() for lazy loading
_original_get = StrategyRegistry.get

def _lazy_get(name: str):
    """Lazy-load a strategy class on first access."""
    cls = _original_get(name)
    if cls is not None:
        return cls
    # Not yet loaded — try lazy import
    if name in _lazy_map:
        module_path, class_name = _lazy_map[name]
        try:
            mod = __import__(module_path, fromlist=[class_name])
            cls = getattr(mod, class_name)
            StrategyRegistry.register(name, cls)
            return cls
        except Exception as e:
            logger.warning(f"Strategy '{name}' failed to lazy-load: {e}")
    return None

StrategyRegistry.get = classmethod(_lazy_get)

# Also patch list_strategies to include unloaded strategies
_original_list_strategies = StrategyRegistry.list_strategies

def _lazy_list_strategies(cls):
    result = _original_list_strategies()
    loaded_names = {r['name'] for r in result}
    # Add unloaded strategies with minimal info
    for name in _lazy_map:
        if name not in loaded_names:
            result.append({
                "name": name,
                "class": _lazy_map[name][1],
                "module": _lazy_map[name][0],
                "description": "",
                "parameters": [],
            })
    return result

StrategyRegistry.list_strategies = classmethod(_lazy_list_strategies)
