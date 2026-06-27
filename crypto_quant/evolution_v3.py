"""
Evolution Engine v3 — Compatibility shim

This module is split into three files for maintainability:
- evolution_core.py: Pareto multi-objective + BayesianOptimizer
- evolution_engine.py: EvolutionEngineV3 core class (evolve_params, batch_evolve)
- evolution_tools.py: P2 extensions (cross_validate, stress_test, detect_decay, etc.)

All external imports of `from evolution_v3 import evo_v3` continue to work.
"""
from evolution_core import dominates, pareto_frontier, multi_objective_fitness, BayesianOptimizer
from evolution_engine import EvolutionEngineV3
from evolution_tools import (  # noqa: F401 — triggers monkey-patch to attach P2 methods
    _transfer_knowledge, _evolve_params_adaptive, _ab_test,
    _cross_validate, _detect_decay, _sensitivity_analysis,
    _stress_test, _notify, _patch_evolution_engine,
)

# Re-initialize the global singleton after all patches are applied
evo_v3 = EvolutionEngineV3()
