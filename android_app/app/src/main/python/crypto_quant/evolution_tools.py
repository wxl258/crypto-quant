"""
Evolution Tools — P2 extensions for EvolutionEngineV3

Contains:
- transfer_knowledge: Cross-strategy parameter transfer
- evolve_params_adaptive: Adaptive Bayesian optimization
- ab_test: A/B testing framework
- cross_validate: Multi-symbol cross validation
- detect_decay: Strategy decay detection
- sensitivity_analysis: Parameter sensitivity analysis
- stress_test: Extreme market stress testing
- notify: Notification system
"""
import sys, os, json, copy
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.store import DataStore
from backtest.engine import BacktestEngine
from strategy import StrategyRegistry
from config import get_db_path, get_backtest_config
from evolution_core import dominates, pareto_frontier, multi_objective_fitness, BayesianOptimizer
from evolution_engine import EvolutionEngineV3


# ============ P2: CROSS-STRATEGY KNOWLEDGE TRANSFER ============

def _transfer_knowledge(self: EvolutionEngineV3, source_strategy: str, target_strategy: str,
               symbol: str = "BTCUSDT", interval: str = "1d") -> Dict:
    """Transfer optimized parameters from source to target strategy.
    
    Maps common parameters (adx_period, atr_mult, roc_period, etc.) between
    strategies that share similar indicator logic. This bootstraps the
    target strategy's evolution with proven parameters from the source.
    """
    source_cls = StrategyRegistry.get(source_strategy)
    target_cls = StrategyRegistry.get(target_strategy)
    if not source_cls or not target_cls:
        return {'error': 'Strategy not found'}

    # Get current best params for source
    source_params = {}
    for entry in self._evolution_log:
        if entry['strategy'] == source_strategy and entry['symbol'] == symbol:
            source_params = entry['best_params']
            break

    if not source_params:
        # Evolve source first if no history
        r = self.evolve_params(source_strategy, symbol, interval)
        source_params = r.get('best_params', {})

    # Map common parameter names
    param_map = {
        'adx_period': ['adx_period'],
        'atr_mult': ['atr_mult', 'atr_stop_mult', 'atr_exit_mult', 'atr_sl_mult'],
        'roc_period': ['roc_period'],
        'roc_threshold': ['roc_threshold'],
        'adx_threshold': ['adx_threshold', 'trend_filter_adx'],
        'atr_period': ['atr_period'],
        'cooldown_bars': ['cooldown_bars'],
    }

    target_param_info = target_cls.get_param_info()
    target_param_names = {p['name'] for p in target_param_info}

    transferred = {}
    for src_key, target_keys in param_map.items():
        if src_key in source_params:
            for tk in target_keys:
                if tk in target_param_names:
                    transferred[tk] = source_params[src_key]

    # Seed the target evolution with transferred params
    result = self.evolve_params(target_strategy, symbol, interval)

    return {
        'source': source_strategy,
        'target': target_strategy,
        'transferred_params': transferred,
        'evolution_result': result,
        'knowledge_transferred': len(transferred),
    }


# ============ P2: ADAPTIVE BAYESIAN OPTIMIZATION ============

def _evolve_params_adaptive(self: EvolutionEngineV3, strategy_name: str, symbol: str = "BTCUSDT",
                   interval: str = "1d", max_iterations: int = 50,
                   convergence_threshold: float = 0.01,
                   train_days: int = 180, test_days: int = 30) -> Dict:
    """Adaptive Bayesian optimization that auto-adjusts exploration.
    
    - Start with high exploration (kappa=3.0)
    - As fitness converges (std < threshold), reduce kappa to exploit
    - If stuck (no improvement for 10 iterations), boost kappa to escape local optima
    """
    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': f'Strategy {strategy_name} not found'}

    param_info = strategy_cls.get_param_info()
    if not param_info:
        return {'error': 'No configurable parameters'}

    df = self.store.load_ohlcv(symbol, interval, limit=50000)
    if df is None or df.empty:
        return {'error': 'No data'}

    train_data = df[df.index >= df.index[-1] - pd.Timedelta(days=train_days)]
    test_data = df[df.index >= df.index[-1] - pd.Timedelta(days=test_days)]

    # Adaptive parameters
    kappa = 3.0  # Start exploratory
    optimizer = BayesianOptimizer(param_info, n_initial=8, kappa=kappa)
    best_fitness = -float('inf')
    best_params = None
    no_improvement_count = 0
    fitness_history = []
    kappa_history = []

    for iteration in range(max_iterations):
        optimizer.kappa = kappa
        candidate = optimizer.suggest()
        fitness, scores = self._evaluate_params(candidate, strategy_cls, train_data, symbol)
        optimizer.observe(candidate, fitness)
        fitness_history.append(fitness)

        if fitness > best_fitness + 0.001:
            best_fitness = fitness
            best_params = copy.deepcopy(candidate)
            no_improvement_count = 0
        else:
            no_improvement_count += 1

        # Adaptive kappa adjustment
        if len(fitness_history) >= 5:
            recent_std = np.std(fitness_history[-5:])
            if recent_std < convergence_threshold:
                kappa = max(0.5, kappa * 0.8)  # Converging → exploit more
            elif no_improvement_count >= 10:
                kappa = min(5.0, kappa * 1.5)  # Stuck → explore more
                no_improvement_count = 0

        kappa_history.append(kappa)

    test_fitness, _ = self._evaluate_params(best_params, strategy_cls, test_data, symbol)

    return {
        'strategy': strategy_name,
        'best_params': best_params,
        'train_fitness': round(best_fitness, 4),
        'test_fitness': round(test_fitness, 4),
        'iterations': max_iterations,
        'final_kappa': round(kappa, 2),
        'fitness_history': [round(f, 4) for f in fitness_history],
        'kappa_history': [round(k, 2) for k in kappa_history],
        'improvements': no_improvement_count,
    }


# ============ P2: A/B TESTING FRAMEWORK ============

def _ab_test(self: EvolutionEngineV3, strategy_name: str, params_a: Dict, params_b: Dict,
            symbol: str = "BTCUSDT", interval: str = "1d",
            test_days: int = 60, split_ratio: float = 0.5) -> Dict:
    """A/B test two parameter sets in parallel on historical data.
    
    Each set gets split_ratio of capital. Returns which is better.
    """
    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': 'Strategy not found'}

    df = self.store.load_ohlcv(symbol, interval, limit=50000)
    if df is None or df.empty:
        return {'error': 'No data'}

    test_data = df[df.index >= df.index[-1] - pd.Timedelta(days=test_days)]
    engine = self._create_engine()

    # Run A
    engine.position_pct = self.backtest_cfg.get('position_pct', 0.3) * split_ratio
    strat_a = strategy_cls(params_a)
    result_a = engine.run(strat_a, test_data, symbol)

    # Run B
    strat_b = strategy_cls(params_b)
    result_b = engine.run(strat_b, test_data, symbol)

    ma = result_a['metrics']
    mb = result_b['metrics']

    # Composite score
    score_a = ma.get('total_return', 0) * 0.4 + ma.get('sharpe_ratio', 0) * 15 - abs(ma.get('max_drawdown', 0)) * 0.3
    score_b = mb.get('total_return', 0) * 0.4 + mb.get('sharpe_ratio', 0) * 15 - abs(mb.get('max_drawdown', 0)) * 0.3

    winner = 'A' if score_a > score_b else 'B'

    return {
        'strategy': strategy_name,
        'test_days': test_days,
        'winner': winner,
        'score_a': round(score_a, 2),
        'score_b': round(score_b, 2),
        'result_a': {k: round(v, 2) if isinstance(v, (int, float)) else v 
                    for k, v in ma.items() if k in ('total_return', 'sharpe_ratio', 'max_drawdown', 'win_rate', 'total_trades')},
        'result_b': {k: round(v, 2) if isinstance(v, (int, float)) else v 
                    for k, v in mb.items() if k in ('total_return', 'sharpe_ratio', 'max_drawdown', 'win_rate', 'total_trades')},
        'recommendation': f'Use parameter set {winner} (score: {max(score_a, score_b):.1f} vs {min(score_a, score_b):.1f})',
    }


# ============ 多币种交叉验证 ============

def _cross_validate(self: EvolutionEngineV3, strategy_name: str, symbols: List[str] = None,
                   interval: str = "1d", train_days: int = 180) -> Dict:
    """Evolve on BTC, validate on ETH/SOL/DOGE/BNB. Select params with best cross-symbol avg."""
    if symbols is None:
        symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT']

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': 'Strategy not found'}

    # Evolve on BTC first
    btc_result = self.evolve_params(strategy_name, 'BTCUSDT', interval, train_days=train_days)
    btc_params = btc_result.get('best_params', {})

    # Validate on all other symbols
    cross_results = {}
    all_scores = []
    for sym in symbols:
        df = self.store.load_ohlcv(sym, interval, limit=50000)
        if df is None or df.empty:
            continue
        test_data = df[df.index >= df.index[-1] - pd.Timedelta(days=60)]
        fitness, scores = self._evaluate_params(btc_params, strategy_cls, test_data, sym)
        cross_results[sym] = {'fitness': round(fitness, 4), 'scores': scores}
        all_scores.append(fitness)

    avg_score = np.mean(all_scores) if all_scores else 0
    generalization = '✅ 强泛化' if avg_score > 0.3 else ('🟡 可接受' if avg_score > 0 else '🔴 弱泛化')

    return {
        'strategy': strategy_name,
        'btc_params': btc_params,
        'cross_results': cross_results,
        'avg_cross_score': round(avg_score, 4),
        'generalization': generalization,
        'valid_symbols': len(cross_results),
    }


# ============ 策略衰减检测 ============

def _detect_decay(self: EvolutionEngineV3, strategy_name: str, symbol: str = "BTCUSDT",
                 interval: str = "1d", window_days: int = 30,
                 threshold: float = 0.3) -> Dict:
    """Monitor rolling performance. Trigger alert if decay detected."""
    df = self.store.load_ohlcv(symbol, interval, limit=50000)
    if df is None or df.empty:
        return {'error': 'No data'}

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': 'Strategy not found'}

    # Get current best params
    params = {}
    for entry in self._evolution_log:
        if entry['strategy'] == strategy_name:
            params = entry['best_params']
            break
    if not params:
        return {'error': 'No evolved params yet. Run evolve first.'}

    # Rolling window analysis
    rolling_metrics = []
    step_days = window_days // 3
    total_days = 180

    for start_offset in range(total_days, window_days - 1, -step_days):
        end = df.index[-1] - pd.Timedelta(days=start_offset - total_days)
        start = end - pd.Timedelta(days=window_days)
        window_data = df[(df.index >= start) & (df.index < end)]

        if len(window_data) < 10:
            continue

        strategy = strategy_cls(params)
        engine = self._create_engine()
        result = engine.run(strategy, window_data, symbol)
        m = result['metrics']
        rolling_metrics.append({
            'period_end': str(end)[:10],
            'return': m.get('total_return', 0),
            'sharpe': m.get('sharpe_ratio', 0),
            'win_rate': m.get('win_rate', 0),
            'trades': m.get('total_trades', 0),
        })

    if len(rolling_metrics) < 2:
        return {'error': 'Not enough data for decay detection'}

    # Detect decay: recent < historical * threshold
    recent = rolling_metrics[-1]
    historical = rolling_metrics[:-1]
    hist_avg_return = np.mean([m['return'] for m in historical])

    decay_detected = recent['return'] < hist_avg_return * threshold
    severity = '🔴 严重' if recent['return'] < 0 else ('🟡 轻微' if decay_detected else '✅ 正常')

    return {
        'strategy': strategy_name,
        'decay_detected': decay_detected,
        'severity': severity,
        'recent_return': recent['return'],
        'historical_avg_return': round(hist_avg_return, 2),
        'rolling_metrics': rolling_metrics,
        'recommendation': '立即触发重新进化' if decay_detected else '策略健康，无需操作',
    }


# ============ 参数敏感性分析 ============

def _sensitivity_analysis(self: EvolutionEngineV3, strategy_name: str, symbol: str = "BTCUSDT",
                         interval: str = "1d", test_days: int = 90) -> Dict:
    """Perturb each parameter ±20% and measure impact on return/sharpe/drawdown."""
    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': 'Strategy not found'}

    param_info = strategy_cls.get_param_info()
    if not param_info:
        return {'error': 'No parameters'}

    # Get baseline params
    params = {}
    for entry in self._evolution_log:
        if entry['strategy'] == strategy_name:
            params = entry['best_params']
            break
    if not params:
        strategy = strategy_cls()
        params = strategy.params

    df = self.store.load_ohlcv(symbol, interval, limit=50000)
    test_data = df[df.index >= df.index[-1] - pd.Timedelta(days=test_days)]

    # Baseline
    base_fitness, _ = self._evaluate_params(params, strategy_cls, test_data, symbol)

    sensitivities = []
    for p in param_info:
        name = p['name']; ptype = p.get('type', 'float')
        if name not in params or ptype == 'str':
            continue

        orig_val = params[name]
        results = []

        for factor in [0.8, 1.0, 1.2]:
            test_params = copy.deepcopy(params)
            if ptype == 'int':
                test_params[name] = int(round(orig_val * factor))
            elif ptype == 'bool':
                test_params[name] = not orig_val if factor != 1.0 else orig_val
            else:
                test_params[name] = orig_val * factor

            fitness, _ = self._evaluate_params(test_params, strategy_cls, test_data, symbol)
            results.append(fitness)

        impact = abs(results[0] - results[2])  # -20% vs +20% difference
        sensitivities.append({
            'param': name,
            'baseline_value': orig_val,
            'impact': round(impact, 4),
            'fitness_80pct': round(results[0], 4),
            'fitness_100pct': round(results[1], 4),
            'fitness_120pct': round(results[2], 4),
        })

    sensitivities.sort(key=lambda x: x['impact'], reverse=True)

    return {
        'strategy': strategy_name,
        'baseline_fitness': round(base_fitness, 4),
        'sensitivities': sensitivities,
        'most_sensitive': sensitivities[0]['param'] if sensitivities else None,
        'least_sensitive': sensitivities[-1]['param'] if sensitivities else None,
    }


# ============ 压力测试 ============

def _stress_test(self: EvolutionEngineV3, strategy_name: str, symbol: str = "BTCUSDT",
                interval: str = "1d") -> Dict:
    """Test strategy under extreme market conditions."""
    df = self.store.load_ohlcv(symbol, interval, limit=50000)
    if df is None or df.empty:
        return {'error': 'No data'}

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        return {'error': 'Strategy not found'}

    params = {}
    for entry in self._evolution_log:
        if entry['strategy'] == strategy_name:
            params = entry['best_params']
            break
    if not params:
        strategy = strategy_cls()
        params = strategy.params

    # Find extreme periods
    close = df['close'].values
    returns = np.diff(close) / close[:-1]

    # Worst 30-day period
    rolling_30d = pd.Series(close).pct_change(30).dropna()
    worst_idx = rolling_30d.idxmin()
    worst_pos = df.index.get_loc(worst_idx) if isinstance(worst_idx, pd.Timestamp) else worst_idx
    crash_start = df.index[max(0, worst_pos - 30)]
    crash_data = df[(df.index >= crash_start) & (df.index <= df.index[worst_pos])]

    # Best 30-day period
    best_idx = rolling_30d.idxmax()
    best_pos = df.index.get_loc(best_idx) if isinstance(best_idx, pd.Timestamp) else best_idx
    rally_start = df.index[max(0, best_pos - 30)]
    rally_data = df[(df.index >= rally_start) & (df.index <= df.index[best_pos])]

    # Recent 90 days (normal)
    normal_data = df[df.index >= df.index[-1] - pd.Timedelta(days=90)]

    results = {}
    for scenario, data, label in [
        ('crash', crash_data, '暴跌'),
        ('rally', rally_data, '暴涨'), 
        ('normal', normal_data, '正常'),
    ]:
        if len(data) < 10:
            continue
        strategy = strategy_cls(params)
        engine = self._create_engine()
        result = engine.run(strategy, data, symbol)
        m = result['metrics']
        results[label] = {
            'return': m.get('total_return', 0),
            'sharpe': m.get('sharpe_ratio', 0),
            'max_dd': m.get('max_drawdown', 0),
            'trades': m.get('total_trades', 0),
            'period': f'{str(data.index[0])[:10]}~{str(data.index[-1])[:10]}',
        }

    # Stress score
    crash_ret = results.get('暴跌', {}).get('return', 0)
    normal_ret = results.get('正常', {}).get('return', 0)
    stress_ratio = crash_ret / max(abs(normal_ret), 1) if normal_ret != 0 else 0
    resilience = '✅ 强韧' if stress_ratio > -1 else ('🟡 可接受' if stress_ratio > -3 else '🔴 脆弱')

    return {
        'strategy': strategy_name,
        'scenarios': results,
        'stress_ratio': round(stress_ratio, 2),
        'resilience': resilience,
    }


# ============ 通知系统 ============

def _notify(self: EvolutionEngineV3, event_type: str, details: Dict) -> Dict:
    """Generate structured notification for key events."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    templates = {
        'evolution_complete': '🧬 进化完成: {strategy} 训练={train_fitness} 测试={test_fitness}',
        'decay_detected': '⚠️ 策略衰减: {strategy} 近期收益={recent}% vs 历史={historical}%',
        'circuit_breaker': '🛑 熔断触发: {reason}',
        'ab_test_result': '📊 A/B测试: {strategy} 胜者={winner} (得分 {score_a} vs {score_b})',
        'stress_test': '💥 压力测试: {strategy} 韧性={resilience}',
        'new_best': '🏆 新最佳参数: {strategy} 适应度={fitness}',
    }

    template = templates.get(event_type, '📢 {event}: {details}')
    try:
        message = template.format(**details, event=event_type)
    except KeyError:
        message = f'📢 {event_type}: {json.dumps(details, default=str)[:200]}'

    notification = {
        'timestamp': timestamp,
        'event': event_type,
        'message': message,
        'details': details,
    }

    # Store in evolution log
    self._evolution_log.append({
        'timestamp': timestamp,
        'type': 'notification',
        'event': event_type,
        'message': message,
    })

    return notification


# ============ MONKEY-PATCH: Attach P2 methods to EvolutionEngineV3 ============

def _patch_evolution_engine():
    """Attach all P2 methods to EvolutionEngineV3 class."""
    EvolutionEngineV3.transfer_knowledge = _transfer_knowledge
    EvolutionEngineV3.evolve_params_adaptive = _evolve_params_adaptive
    EvolutionEngineV3.ab_test = _ab_test
    EvolutionEngineV3.cross_validate = _cross_validate
    EvolutionEngineV3.detect_decay = _detect_decay
    EvolutionEngineV3.sensitivity_analysis = _sensitivity_analysis
    EvolutionEngineV3.stress_test = _stress_test
    EvolutionEngineV3.notify = _notify

_patch_evolution_engine()
