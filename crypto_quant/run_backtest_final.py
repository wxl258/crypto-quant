"""
全面优化后回测 — 30天/90天 BTC/ETH + SOL/DOGE/BNB 日线 + MetaStrategy
"""
import sys, os, json, time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd, numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.store import DataStore
from backtest.engine import BacktestEngine
from strategy import StrategyRegistry
from config import get_db_path, get_backtest_config


def load_data(store, symbol, interval, window_days=None, limit=50000):
    df = store.load_ohlcv(symbol, interval, limit=limit)
    if df is None or df.empty:
        return None
    if window_days:
        cutoff = df.index[-1] - pd.Timedelta(days=window_days)
        df = df[df.index >= cutoff]
    return df if not df.empty else None


def run_one(args):
    sname, symbol, interval, window_days, backtest_cfg = args
    try:
        store = DataStore(get_db_path())
        limit = 10000 if interval == '1h' else 50000
        df = load_data(store, symbol, interval, window_days, limit)
        if df is None or df.empty:
            return None
        scls = StrategyRegistry.get(sname)
        if scls is None:
            return None
        engine = BacktestEngine(
            initial_capital=backtest_cfg.get('initial_capital', 10000),
            commission=backtest_cfg.get('commission', 0.0005),
            slippage=backtest_cfg.get('slippage', 0.0002),
            funding_rate=backtest_cfg.get('funding_rate', 0.0001),
            slippage_model=backtest_cfg.get('slippage_model', 'volume'),
            position_pct=backtest_cfg.get('position_pct', 0.3),
            default_leverage=backtest_cfg.get('default_leverage', 3),
        )
        strategy = scls()
        result = engine.run(strategy, df, symbol)
        m = result['metrics']
        ret_val = m.get('total_return', 0)
        ret_dec = ret_val / 100.0
        if ret_dec > -1 and window_days and window_days > 0:
            ann = ((1 + ret_dec) ** (365.0 / window_days) - 1) * 100
        else:
            ann = -100 if ret_dec <= -1 else 0
        return {
            'strategy': sname, 'symbol': symbol, 'interval': interval,
            'window_days': window_days or 0,
            'total_return': round(ret_val, 2),
            'annual_return': round(ann, 2),
            'sharpe_ratio': round(m.get('sharpe_ratio', 0), 2),
            'max_drawdown': round(m.get('max_drawdown', 0), 2),
            'win_rate': round(m.get('win_rate', 0), 1),
            'total_trades': m.get('total_trades', 0),
            'data_rows': len(df),
            'error': None,
        }
    except Exception as e:
        return {'strategy': sname, 'symbol': symbol, 'interval': interval,
                'window_days': window_days or 0, 'error': str(e)[:120]}


def main():
    print("=" * 70)
    print("  全面优化后回测")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    backtest_cfg = get_backtest_config()
    strategies = StrategyRegistry.list_strategies()
    skip = {'ai_assisted', 'multi_agent'}
    fast = [s for s in strategies if s['name'] not in skip]
    print(f"  策略: {len(fast)} 个")

    # BTC/ETH 30天+90天
    tasks = []
    for s in fast:
        for sym in ['BTCUSDT', 'ETHUSDT']:
            for iv in ['1h', '4h', '1d']:
                for w in [30, 90]:
                    tasks.append((s['name'], sym, iv, w, backtest_cfg))

    # SOL/DOGE/BNB 日线 1年+全量
    for s in fast:
        for sym in ['SOLUSDT', 'DOGEUSDT', 'BNBUSDT']:
            tasks.append((s['name'], sym, '1d', 365, backtest_cfg))
            tasks.append((s['name'], sym, '1d', None, backtest_cfg))

    total = len(tasks)
    print(f"  任务: {total} | 并行: 8")
    print("-" * 70)

    all_results = []
    count = 0
    t0 = time.time()

    with ProcessPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(run_one, t): t for t in tasks}
        for f in as_completed(futures):
            count += 1
            r = f.result()
            if r:
                all_results.append(r)
                if r.get('error'):
                    print(f"  [{count:4d}/{total}] ❌ {r['strategy']}")
                else:
                    print(f"  [{count:4d}/{total}] {r['strategy']:25s} {r['symbol']:8s} {r['interval']:3s} {r['window_days']:4d}d → {r['total_return']:+7.1f}% 夏普={r['sharpe_ratio']:+5.2f} 交易={r['total_trades']}")

    elapsed = time.time() - t0
    print(f"\n  ⏱ {elapsed:.0f}s")

    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backtest_final.json')
    with open(output, 'w') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"  ✅ {output} | {len(all_results)} 条记录")
    return all_results


if __name__ == '__main__':
    main()
