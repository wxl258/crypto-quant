"""
Unit tests for backtest engine and metrics.
"""
import pytest
import pandas as pd
import numpy as np
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics
from strategy.base import Strategy, Signal, SignalType


def _make_ohlcv(n: int = 200, trend: str = "up", seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.default_rng(seed)
    if trend == "up":
        base = np.linspace(100, 150, n)
    elif trend == "down":
        base = np.linspace(150, 100, n)
    else:  # range
        base = np.full(n, 120.0) + rng.normal(0, 3, n).cumsum() * 0.3

    noise = rng.normal(0, 1, n) * 2
    close = base + noise
    high = close + np.abs(rng.normal(0, 1.5, n))
    low = close - np.abs(rng.normal(0, 1.5, n))
    open_ = close - rng.normal(0, 0.5, n)
    volume = np.abs(rng.normal(1000, 200, n))

    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    }, index=idx)


class AlwaysBuyStrategy(Strategy):
    """Test strategy that buys on every bar if flat, never exits."""

    def _default_params(self):
        return {}

    def init(self):
        pass

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        if self._position == 0:
            return Signal(SignalType.BUY, "TEST", price, reason="always buy")
        return Signal(SignalType.HOLD, "TEST", price)


class BuyAndSellStrategy(Strategy):
    """Test strategy: buy at start, sell at end."""

    def _default_params(self):
        return {}

    def init(self):
        pass

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]
        n = len(self.data)
        if i == 10 and self._position == 0:
            return Signal(SignalType.BUY, "TEST", price, reason="buy early")
        if i == n - 20 and self._position == 1:
            return Signal(SignalType.SELL, "TEST", price, reason="sell late")
        return Signal(SignalType.HOLD, "TEST", price)


class TestBacktestEngine:
    """Tests for BacktestEngine."""

    def test_basic_run(self):
        df = _make_ohlcv(200, "up")
        engine = BacktestEngine(initial_capital=10000, commission=0.0004, slippage=0.0001)
        strategy = AlwaysBuyStrategy()
        result = engine.run(strategy, df)
        assert result is not None
        assert 'equity_curve' in result
        assert len(result['equity_curve']) > 0

    def test_commission_deducted(self):
        df = _make_ohlcv(500, "up")
        engine = BacktestEngine(initial_capital=10000, commission=0.001, slippage=0.0)
        strategy = BuyAndSellStrategy()
        result = engine.run(strategy, df)
        final_equity = result['equity_curve']['equity'].iloc[-1]
        assert final_equity > 10000  # profitable in uptrend

    def test_slippage_applied(self):
        df = _make_ohlcv(200, "up")
        engine_no_slip = BacktestEngine(initial_capital=10000, commission=0, slippage=0)
        engine_slip = BacktestEngine(initial_capital=10000, commission=0, slippage=0.01)

        strategy = AlwaysBuyStrategy()
        r_no = engine_no_slip.run(strategy, df.copy())
        r_slip = engine_slip.run(strategy, df.copy())

        # With slippage, equity should be lower
        assert r_slip['equity_curve']['equity'].iloc[-1] < r_no['equity_curve']['equity'].iloc[-1]

    def test_initial_capital(self):
        df = _make_ohlcv(100, "range")
        engine = BacktestEngine(initial_capital=50000, commission=0, slippage=0)
        strategy = AlwaysBuyStrategy()
        result = engine.run(strategy, df)
        assert result['initial_capital'] == 50000

    def test_run_multiple_returns_results(self):
        df = _make_ohlcv(100, "up")
        engine = BacktestEngine(initial_capital=10000)
        param_list = [
            {'fast_period': 5, 'slow_period': 20},
            {'fast_period': 10, 'slow_period': 30},
        ]
        from strategy.dual_ma import DualMAStrategy
        results = engine.run_multiple(DualMAStrategy, param_list, df)
        assert len(results) == 2


class TestBacktestMetrics:
    """Tests for calculate_metrics."""

    def test_profitable_curve(self):
        idx = pd.date_range("2024-01-01", periods=100, freq="1h")
        equity = pd.Series(np.linspace(10000, 15000, 100), index=idx)
        trades = pd.DataFrame({
            'entry_time': [idx[10]],
            'exit_time': [idx[90]],
            'side': ['LONG'],
            'pnl': [5000.0],
            'pnl_pct': [50.0],
        })
        metrics = calculate_metrics(equity, trades, initial_capital=10000)
        assert metrics['total_return'] > 0
        assert metrics['total_trades'] == 1

    def test_losing_curve(self):
        idx = pd.date_range("2024-01-01", periods=100, freq="1h")
        equity = pd.Series(np.linspace(10000, 5000, 100), index=idx)
        trades = pd.DataFrame({
            'entry_time': [idx[10]],
            'exit_time': [idx[90]],
            'side': ['LONG'],
            'pnl': [-5000.0],
            'pnl_pct': [-50.0],
        })
        metrics = calculate_metrics(equity, trades, initial_capital=10000)
        assert metrics['total_return'] < 0
        assert metrics['max_drawdown'] != 0  # drawdown is negative percentage

    def test_flat_curve(self):
        idx = pd.date_range("2024-01-01", periods=100, freq="1h")
        equity = pd.Series(np.full(100, 10000.0), index=idx)
        trades = pd.DataFrame()
        metrics = calculate_metrics(equity, trades, initial_capital=10000)
        assert metrics['total_return'] == 0.0
        assert metrics['max_drawdown'] == 0.0

    def test_win_rate_and_profit_factor(self):
        idx = pd.date_range("2024-01-01", periods=20, freq="1d")
        equity = pd.Series(np.linspace(10000, 11000, 20), index=idx)
        trades = pd.DataFrame([
            {'entry_time': idx[1], 'exit_time': idx[3], 'side': 'LONG', 'pnl': 200.0, 'pnl_pct': 2.0},
            {'entry_time': idx[4], 'exit_time': idx[6], 'side': 'LONG', 'pnl': 300.0, 'pnl_pct': 3.0},
            {'entry_time': idx[7], 'exit_time': idx[9], 'side': 'SHORT', 'pnl': -80.0, 'pnl_pct': -0.8},
            {'entry_time': idx[10], 'exit_time': idx[12], 'side': 'LONG', 'pnl': 150.0, 'pnl_pct': 1.5},
            {'entry_time': idx[13], 'exit_time': idx[15], 'side': 'LONG', 'pnl': -50.0, 'pnl_pct': -0.5},
        ])
        metrics = calculate_metrics(equity, trades, initial_capital=10000)
        assert metrics['total_return'] > 0
        assert 'win_rate' in metrics
        assert 'profit_factor' in metrics
        assert metrics['total_trades'] == 5
