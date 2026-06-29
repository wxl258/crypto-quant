import sys, os, json, time, random, copy, math
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.store import DataStore
from backtest.engine import BacktestEngine
from strategy import StrategyRegistry
from config import get_db_path, get_backtest_config
from evolution_core import dominates, pareto_frontier, multi_objective_fitness, BayesianOptimizer

# ============ EVOLUTION ENGINE v3 ============

_MAX_EVOLUTION_LOG = 1000

class EvolutionEngineV3:
    """Walk-Forward + Pareto + Bayesian self-evolution engine."""

    def __init__(self):
        self.store = DataStore(get_db_path())
        self.backtest_cfg = get_backtest_config()
        self._evolution_log = []

    def _create_engine(self) -> BacktestEngine:
        return BacktestEngine(
            initial_capital=self.backtest_cfg.get('initial_capital', 10000),
            commission=self.backtest_cfg.get('commission', 0.0005),
            slippage=self.backtest_cfg.get('slippage', 0.0002),
            funding_rate=self.backtest_cfg.get('funding_rate', 0.0001),
            slippage_model=self.backtest_cfg.get('slippage_model', 'volume'),
        )

    def _evaluate_params(self, params: Dict, strategy_cls, data: pd.DataFrame,
                         symbol: str) -> Tuple[float, Dict]:
        """Evaluate a parameter set, returning composite fitness + scores.
        Includes L2 regularization penalty to prevent overfitting."""
        try:
            strategy = strategy_cls(params)
            engine = self._create_engine()
            result = engine.run(strategy, data, symbol)
            fitness, scores = multi_objective_fitness(result['metrics'])

            # L2 regularization: penalize extreme parameter values
            reg_penalty = 0.0
            param_info = strategy_cls.get_param_info()
            for p in param_info:
                val = params.get(p['name'], 0)
                lo = p.get('min', 0); hi = p.get('max', 10)
                if hi == lo: continue
                ptype = p.get('type', 'float')
                if ptype in ('int', 'float'):
                    # Penalize being far from midpoint (encourage conservative params)
                    mid = (lo + hi) / 2
                    normalized_dev = abs(float(val) - mid) / ((hi - lo) / 2)
                    reg_penalty += normalized_dev * 0.05  # stronger penalty

            fitness -= reg_penalty
            return max(fitness, -10.0), {**scores, 'metrics': result['metrics'], 'reg_penalty': reg_penalty}
        except Exception:
            return -10.0, {}

    def evolve_params(self, strategy_name: str, symbol: str = "BTCUSDT",
                      interval: str = "1h", n_iterations: int = 30,
                      train_days: int = 360, test_days: int = 60,
                      n_walk_forward: int = 3) -> Dict:
        """
        Evolve optimal parameters with Walk-Forward + Bayesian optimization.

        Args:
            strategy_name: Strategy to optimize
            n_iterations: Bayesian optimization iterations
            n_walk_forward: Number of walk-forward folds (3 = most robust)
        """
        strategy_cls = StrategyRegistry.get(strategy_name)
        if not strategy_cls:
            return {'error': f'Strategy {strategy_name} not found', 'status': 'failed'}

        param_info = strategy_cls.get_param_info()
        if not param_info:
            return {'error': 'No configurable parameters', 'status': 'failed'}

        # Load full data
        df = self.store.load_ohlcv(symbol, interval, limit=50000)
        if df is None or df.empty:
            return {'error': 'No data', 'status': 'failed'}

        # ===== WALK-FORWARD SETUP =====
        total_window = train_days + test_days * n_walk_forward
        base_end = df.index[-1]
        base_start = base_end - pd.Timedelta(days=total_window)

        folds = []
        for fold in range(n_walk_forward):
            train_end = base_end - pd.Timedelta(days=test_days * (n_walk_forward - fold))
            train_start = train_end - pd.Timedelta(days=train_days)
            test_start = train_end
            test_end = test_start + pd.Timedelta(days=test_days)

            train_data = df[(df.index >= train_start) & (df.index < train_end)]
            test_data = df[(df.index >= test_start) & (df.index < test_end)]

            if len(train_data) < 20 or len(test_data) < 5:
                continue

            folds.append({
                'fold': fold,
                'train_start': str(train_data.index[0])[:10],
                'train_end': str(train_data.index[-1])[:10],
                'test_start': str(test_data.index[0])[:10],
                'test_end': str(test_data.index[-1])[:10],
                'train_data': train_data,
                'test_data': test_data,
            })

        if len(folds) < 2:
            return {'error': f'Not enough data for walk-forward (need {n_walk_forward} folds, got {len(folds)})', 'status': 'failed'}

        # ===== BAYESIAN OPTIMIZATION per fold =====
        all_pareto = []
        fold_results = []

        for fold_info in folds:
            optimizer = BayesianOptimizer(param_info, n_initial=8)
            best_fitness = -float('inf')
            best_params = None

            for iteration in range(n_iterations):
                candidate = optimizer.suggest()
                fitness, scores = self._evaluate_params(
                    candidate, strategy_cls, fold_info['train_data'], symbol
                )
                optimizer.observe(candidate, fitness)

                if fitness > best_fitness:
                    best_fitness = fitness
                    best_params = copy.deepcopy(candidate)

            # Out-of-sample test on this fold
            test_fitness, test_scores = self._evaluate_params(
                best_params, strategy_cls, fold_info['test_data'], symbol
            )

            fold_results.append({
                'fold': fold_info['fold'],
                'train_period': f"{fold_info['train_start']}~{fold_info['train_end']}",
                'test_period': f"{fold_info['test_start']}~{fold_info['test_end']}",
                'train_fitness': round(best_fitness, 4),
                'test_fitness': round(test_fitness, 4),
                'best_params': copy.deepcopy(best_params),
                'test_scores': test_scores,
            })

            all_pareto.append({
                'params': best_params,
                'fitness': test_fitness,
                'scores': test_scores,
                'fold': fold_info['fold'],
            })

        # ===== PARETO FRONTIER SELECTION =====
        frontier = pareto_frontier(all_pareto)
        if not frontier:
            frontier = all_pareto

        final_best = max(frontier, key=lambda x: x['fitness'])

        # Average metrics: training from all folds, test from LAST fold only (most relevant)
        avg_train_fitness = np.mean([f['train_fitness'] for f in fold_results])
        last_fold_test = fold_results[-1]['test_fitness']  # Only the most recent fold
        std_test_fitness = np.std([f['test_fitness'] for f in fold_results])
        
        # Overfit ratio: train / last_fold_test (closer to 1.0 = better)
        overfit_ratio = avg_train_fitness / max(abs(last_fold_test), 0.001)

        # Record log
        self._evolution_log.append({
            'timestamp': datetime.now().isoformat(),
            'strategy': strategy_name, 'symbol': symbol, 'interval': interval,
            'best_params': final_best['params'],
            'avg_train_fitness': round(avg_train_fitness, 4),
            'last_test_fitness': round(last_fold_test, 4),
            'std_test_fitness': round(std_test_fitness, 4),
            'n_folds': len(folds),
            'n_iterations': n_iterations,
            'fold_results': fold_results,
        })

        # Persist to SQLite
        try:
            generation = len(self._evolution_log)
            self.store.save_evolution_log({
                'generation': generation,
                'best_fitness': round(last_fold_test, 4),
                'best_params': final_best['params'],
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to persist evolution log: {e}")

        # Trim evolution log to prevent unbounded memory growth
        if len(self._evolution_log) > _MAX_EVOLUTION_LOG:
            self._evolution_log = self._evolution_log[-500:]

        return {
            'strategy': strategy_name,
            'status': 'completed',
            'best_params': final_best['params'],
            'avg_train_fitness': round(avg_train_fitness, 4),
            'last_test_fitness': round(last_fold_test, 4),
            'test_fitness_std': round(std_test_fitness, 4),
            'overfit_ratio': round(overfit_ratio, 2),
            'n_folds': len(folds),
            'n_iterations': n_iterations,
            'pareto_frontier_size': len(frontier),
            'fold_results': fold_results,
            'diagnosis': '✅ 无过拟合' if abs(overfit_ratio) < 2 else 
                         '⚠️ 轻微过拟合' if abs(overfit_ratio) < 5 else
                         '🔴 严重过拟合',
        }

    def batch_evolve(self, symbols: List[str] = None, interval: str = "1d",
                     n_iterations: int = 20) -> List[Dict]:
        """Evolve all strategies on all symbols."""
        if symbols is None:
            symbols = ['BTCUSDT', 'ETHUSDT']

        strategies = StrategyRegistry.list_strategies()
        skip = {'ai_assisted', 'multi_agent'}

        results = []
        total = len([s for s in strategies if s['name'] not in skip]) * len(symbols)
        current = 0

        for s_info in strategies:
            sname = s_info['name']
            if sname in skip:
                continue
            for sym in symbols:
                current += 1
                print(f'  [{current}/{total}] {sname:24s} {sym:8s} ...', end=' ', flush=True)
                t0 = time.time()
                try:
                    r = self.evolve_params(sname, sym, interval, n_iterations=n_iterations)
                    elapsed = time.time() - t0
                    diag = r.get('diagnosis', '')
                    print(f'训练={r.get("avg_train_fitness",0):.2f} 测试={r.get("avg_test_fitness",0):.2f} {diag} ({elapsed:.0f}s)')
                    results.append(r)
                except Exception as e:
                    print(f'❌ {str(e)[:50]}')
                    results.append({'strategy': sname, 'symbol': sym, 'error': str(e)[:100]})

        return results

    def get_evolution_log(self) -> List[Dict]:
        return self._evolution_log
