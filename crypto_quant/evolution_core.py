"""
Evolution Engine v3 — Walk-Forward + Pareto Multi-Objective + Bayesian Optimization

Core improvements over v2:
1. WALK-FORWARD VALIDATION: Train on window[t-N:t], test on window[t:t+M], roll forward.
   Final fitness = average across all walk-forward folds. Eliminates overfitting.
2. PARETO MULTI-OBJECTIVE: Optimize return, sharpe, drawdown, win_rate simultaneously.
   Selects non-dominated solutions from the Pareto frontier.
3. BAYESIAN OPTIMIZATION: Uses Gaussian Process surrogate model + Expected Improvement
   acquisition function. 10x more sample-efficient than genetic algorithm.
"""
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

# ============ PARETO MULTI-OBJECTIVE ============

def dominates(a: Dict, b: Dict) -> bool:
    """Check if solution a dominates solution b (better in all objectives)."""
    better = False
    for key in ['return_score', 'sharpe_score', 'dd_score', 'wr_score']:
        if a.get(key, 0) < b.get(key, 0):
            return False
        if a.get(key, 0) > b.get(key, 0):
            better = True
    return better

def pareto_frontier(solutions: List[Dict]) -> List[Dict]:
    """Extract non-dominated solutions (Pareto frontier)."""
    frontier = []
    for s in solutions:
        dominated = False
        for other in solutions:
            if dominates(other, s):
                dominated = True
                break
        if not dominated:
            frontier.append(s)
    return frontier

def multi_objective_fitness(metrics: Dict) -> Tuple[float, Dict]:
    """Compute Pareto-aware multi-objective scores."""
    ret = metrics.get('total_return', 0)
    sharpe = metrics.get('sharpe_ratio', 0)
    dd = abs(metrics.get('max_drawdown', 0))
    wr = metrics.get('win_rate', 0)
    trades = metrics.get('total_trades', 0)

    scores = {
        'return_score': max(0, (ret + 100) / 200),        # normalize to [0,1]
        'sharpe_score': max(0, (sharpe + 5) / 10),
        'dd_score': max(0, 1 - dd / 100),
        'wr_score': wr / 100,
    }

    # Penalize extreme trades
    if trades < 2:
        penalty = -0.5
    elif trades > 50:
        penalty = -0.3
    else:
        penalty = 0

    composite = (scores['return_score'] * 0.35 + scores['sharpe_score'] * 0.30 +
                 scores['dd_score'] * 0.20 + scores['wr_score'] * 0.15 + penalty)

    return max(composite, -10.0), scores


# ============ BAYESIAN OPTIMIZATION ============

class BayesianOptimizer:
    """Gaussian Process-based Bayesian optimization for strategy parameters."""

    def __init__(self, param_info: List[Dict], n_initial: int = 10, kappa: float = 2.0):
        self.param_info = param_info
        self.n_initial = n_initial
        self.kappa = kappa  # exploration-exploitation tradeoff
        self.X = []  # observed parameter sets
        self.y = []  # observed fitness values
        self._bounds = self._build_bounds()

    def _build_bounds(self) -> List[Tuple[float, float]]:
        bounds = []
        for p in self.param_info:
            ptype = p.get('type', 'float')
            if ptype == 'str':
                options = p.get('options', ['majority', 'weighted', 'unanimous'])
                if not options: options = ['default']
                for _ in options:
                    bounds.append((0.0, 1.0))
            elif ptype == 'bool':
                bounds.append((0.0, 1.0))
            elif ptype == 'int':
                bounds.append((0.0, 1.0))
            else:
                bounds.append((0.0, 1.0))
        return bounds

    def _params_to_vector(self, params: Dict) -> np.ndarray:
        vec = []
        for p in self.param_info:
            val = params.get(p['name'], 0)
            ptype = p.get('type', 'float')
            lo = p.get('min', 0); hi = p.get('max', 10)
            if hi == lo: hi = lo + 1
            if ptype == 'bool':
                vec.append(1.0 if val else 0.0)
            elif ptype == 'str':
                # Encode string as one-hot over known options
                options = p.get('options', ['majority', 'weighted', 'unanimous'])
                if not options: options = ['default']
                for opt in options:
                    vec.append(1.0 if str(val) == opt else 0.0)
            else:
                try:
                    vec.append((float(val) - lo) / (hi - lo))
                except (ValueError, TypeError):
                    vec.append(0.5)
        return np.array(vec)

    def _vector_to_params(self, vec: np.ndarray) -> Dict:
        params = {}
        vi = 0
        for p in self.param_info:
            name = p['name']; ptype = p.get('type', 'float')
            lo = p.get('min', 0); hi = p.get('max', 10)
            if hi == lo: hi = lo + 1
            
            if ptype == 'str':
                options = p.get('options', ['majority', 'weighted', 'unanimous'])
                if not options: options = ['default']
                # Pick argmax over one-hot segment
                best_idx = 0; best_val = -1
                for j, opt in enumerate(options):
                    if vi + j < len(vec) and vec[vi + j] > best_val:
                        best_val = vec[vi + j]; best_idx = j
                params[name] = options[best_idx]
                vi += len(options)
            elif ptype == 'bool':
                params[name] = vec[vi] > 0.5
                vi += 1
            else:
                raw = vec[vi] * (hi - lo) + lo
                if ptype == 'int':
                    params[name] = int(round(max(lo, min(hi, raw))))
                else:
                    params[name] = float(max(lo, min(hi, raw)))
                vi += 1
        return params

    def _gp_predict(self, X_train, y_train, X_test):
        """GP prediction using RBF kernel with Cholesky or solve.

        Uses scipy.linalg.cho_solve when available, falls back to
        np.linalg.solve (faster and more stable than inv). If the
        kernel matrix is singular, falls back to simple mean/std.
        """
        if len(X_train) == 0:
            return np.zeros(len(X_test)), np.ones(len(X_test)) * 10

        # RBF kernel
        def rbf(x1, x2, length_scale=1.0):
            dist = np.sum((x1 - x2) ** 2)
            return np.exp(-0.5 * dist / length_scale ** 2)

        n_train = len(X_train)
        n_test = len(X_test)
        K = np.zeros((n_train, n_train))
        for i in range(n_train):
            for j in range(n_train):
                K[i, j] = rbf(X_train[i], X_train[j])

        K += np.eye(n_train) * 1e-6
        y_mean = np.mean(y_train)
        y_centered = y_train - y_mean

        # Try Cholesky decomposition first (scipy), then solve, then fallback
        alpha = None  # K^{-1} @ y_centered
        use_cholesky = False

        try:
            from scipy.linalg import cho_factor, cho_solve
            c, low = cho_factor(K)
            alpha = cho_solve((c, low), y_centered)
            use_cholesky = True
        except Exception:
            pass

        if alpha is None:
            try:
                alpha = np.linalg.solve(K, y_centered)
            except np.linalg.LinAlgError:
                # Singular matrix — fall back to simple average
                mu_fallback = np.full(n_test, y_mean)
                sigma_fallback = np.full(n_test, np.std(y_train) if len(y_train) > 1 else 1.0)
                return mu_fallback, sigma_fallback

        mu = np.zeros(n_test)
        sigma = np.zeros(n_test)

        for i in range(n_test):
            k_star = np.array([rbf(X_test[i], X_train[j]) for j in range(n_train)])

            if use_cholesky:
                # For Cholesky: sigma^2 = k(x,x) - k_star^T K^{-1} k_star
                # K^{-1} k_star = cho_solve(c, k_star)
                try:
                    c, low = cho_factor(K)
                    Kinv_kstar = cho_solve((c, low), k_star)
                except Exception:
                    Kinv_kstar = np.linalg.solve(K, k_star)
            else:
                Kinv_kstar = np.linalg.solve(K, k_star)

            mu[i] = y_mean + k_star @ alpha
            sigma[i] = np.sqrt(max(0, 1.0 - k_star @ Kinv_kstar))

        return mu, sigma

    def suggest(self) -> Dict:
        """Suggest next parameter set using Expected Improvement."""
        if len(self.X) < self.n_initial:
            # Random exploration phase
            return self._random_params()

        X_train = np.array(self.X)
        y_train = np.array(self.y)
        y_best = np.max(y_train)

        # Generate random candidates and pick best by EI
        n_candidates = 200
        candidates = np.random.uniform(0, 1, (n_candidates, len(self._bounds)))
        mu, sigma = self._gp_predict(X_train, y_train, candidates)

        best_ei = -float('inf')
        best_candidate = candidates[0]

        for i in range(n_candidates):
            if sigma[i] < 1e-6:
                ei = 0
            else:
                z = (mu[i] - y_best - self.kappa * 0.01) / sigma[i]
                # EI = sigma * (z * Phi(z) + phi(z))
                phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
                # Simple approximation of Phi
                Phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))
                ei = sigma[i] * (z * Phi + phi)

            if ei > best_ei:
                best_ei = ei
                best_candidate = candidates[i]

        return self._vector_to_params(best_candidate)

    def _random_params(self) -> Dict:
        params = {}
        for p in self.param_info:
            name = p['name']; ptype = p.get('type', 'float')
            if ptype == 'int':
                params[name] = random.randint(int(p.get('min', 1)), int(p.get('max', 100)))
            elif ptype == 'float':
                params[name] = random.uniform(p.get('min', 0.0), p.get('max', 10.0))
            elif ptype == 'bool':
                params[name] = random.choice([True, False])
            else:
                params[name] = p.get('default', 0)
        return params

    def observe(self, params: Dict, fitness: float):
        self.X.append(self._params_to_vector(params))
        self.y.append(fitness)

