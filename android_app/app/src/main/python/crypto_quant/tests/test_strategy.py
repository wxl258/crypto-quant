"""
Unit tests for strategy module — base class and indicator helpers.
"""
import pytest
import pandas as pd
import numpy as np
from strategy.base import Strategy, Signal, SignalType, StrategyRegistry


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = np.linspace(100, 120, n) + rng.normal(0, 2, n).cumsum() * 0.3
    close = base
    high = close + np.abs(rng.normal(0, 1.5, n))
    low = close - np.abs(rng.normal(0, 1.5, n))
    open_ = close - rng.normal(0, 0.5, n)
    volume = np.abs(rng.normal(1000, 200, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    }, index=idx)


class TestSignal:
    """Tests for Signal dataclass."""

    def test_signal_creation(self):
        s = Signal(
            signal_type=SignalType.BUY,
            symbol="BTCUSDT",
            price=65000.0,
            reason="test buy",
            quantity=0.1,
            stop_loss=61750.0,
            take_profit=71500.0,
        )
        assert s.signal_type == SignalType.BUY
        assert s.symbol == "BTCUSDT"
        assert s.price == 65000.0
        assert s.quantity == 0.1
        assert s.stop_loss == 61750.0
        assert s.take_profit == 71500.0
        assert s.reason == "test buy"

    def test_signal_defaults(self):
        s = Signal(SignalType.HOLD, "", 0)
        assert s.signal_type == SignalType.HOLD
        assert s.quantity is None  # default is None, not 0.0

    def test_signal_types(self):
        assert SignalType.BUY.value == "BUY"
        assert SignalType.SELL.value == "SELL"
        assert SignalType.CLOSE_LONG.value == "CLOSE_LONG"
        assert SignalType.CLOSE_SHORT.value == "CLOSE_SHORT"
        assert SignalType.HOLD.value == "HOLD"


class TestStrategyBase:
    """Tests for base Strategy class."""

    def _make_strategy(self, params=None):
        class TestStrategy(Strategy):
            def _default_params(self):
                return {"period": 14, "threshold": 0.5}

            def init(self):
                pass

            def next(self, i):
                return Signal(SignalType.HOLD, "", self.data['close'].iloc[i])

        return TestStrategy(params)

    def test_params_merge(self):
        s = self._make_strategy({"period": 20})
        assert s.get_param("period") == 20
        assert s.get_param("threshold") == 0.5  # default preserved

    def test_params_default_only(self):
        s = self._make_strategy()
        assert s.get_param("period") == 14
        assert s.get_param("threshold") == 0.5

    def test_get_param_with_default(self):
        s = self._make_strategy()
        assert s.get_param("nonexistent", 42) == 42

    def test_set_and_get_position(self):
        s = self._make_strategy()
        assert s.get_position() == 0
        s.set_position(1, 65000.0)
        assert s.get_position() == 1

    def test_set_data(self):
        s = self._make_strategy()
        df = _make_ohlcv(100)
        s.set_data(df)
        assert s.data is not None
        assert len(s.data) == 100

    def test_indicator_helpers_sma(self):
        s = self._make_strategy()
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        sma5 = s.sma(data, 5)
        assert len(sma5) == len(data)
        assert np.isnan(sma5[3])
        assert abs(sma5[4] - 3.0) < 1e-9  # mean of 1-5
        assert abs(sma5[9] - 8.0) < 1e-9  # mean of 6-10

    def test_indicator_helpers_ema(self):
        s = self._make_strategy()
        data = np.ones(20) * 100.0
        ema10 = s.ema(data, 10)
        assert len(ema10) == len(data)
        assert abs(ema10[-1] - 100.0) < 1e-9

    def test_indicator_helpers_rsi(self):
        s = self._make_strategy()
        data = np.linspace(100, 150, 100)
        rsi = s.rsi(data, 14)
        assert len(rsi) == len(data)
        assert not np.isnan(rsi[-1])
        assert rsi[-1] > 50  # uptrend → RSI > 50

    def test_indicator_helpers_bollinger(self):
        s = self._make_strategy()
        data = np.array([10.0, 10.5, 10.2, 9.8, 10.1] * 20)
        mid, upper, lower = s.bollinger_bands(data, period=20)
        assert len(mid) == len(data)
        assert upper[-1] > mid[-1] > lower[-1]

    def test_indicator_helpers_atr(self):
        s = self._make_strategy()
        df = _make_ohlcv(100)
        s.set_data(df)
        atr = s.atr(df['high'].values, df['low'].values, df['close'].values, 14)
        assert len(atr) == len(df)
        assert atr[-1] > 0

    def test_bollinger_bands_return_order(self):
        s = self._make_strategy()
        data = np.array([10.0, 10.5, 10.2, 9.8, 10.1] * 20)
        mid, upper, lower = s.bollinger_bands(data, period=20)
        # Verify return order: (mid, upper, lower)
        for i in range(20 - 1, len(data)):
            if np.isnan(mid[i]):
                continue
            assert mid[i] <= upper[i], f"mid[{i}]={mid[i]} > upper[{i}]={upper[i]}"
            assert lower[i] <= mid[i], f"lower[{i}]={lower[i]} > mid[{i}]={mid[i]}"

    def test_rsi_nan_initial(self):
        s = self._make_strategy()
        data = np.linspace(100, 150, 100)
        period = 14
        rsi = s.rsi(data, period)
        # First (period) values should be NaN
        for i in range(period):
            assert np.isnan(rsi[i]), f"rsi[{i}] should be NaN, got {rsi[i]}"
        # Values after period should not be NaN
        for i in range(period, len(rsi)):
            assert not np.isnan(rsi[i]), f"rsi[{i}] should not be NaN"

    def test_signal_quality_score(self):
        s = self._make_strategy()
        df = _make_ohlcv(200)
        s.set_data(df)
        # Test at a few indices that score is in [0, 1]
        for i in [50, 100, 150]:
            score = s.signal_quality_score(i, 'LONG', df['close'].iloc[i])
            assert 0.0 <= score <= 1.0, f"signal_quality_score({i})={score} not in [0,1]"


class TestStrategyRegistry:
    """Tests for StrategyRegistry."""

    def setup_method(self):
        StrategyRegistry._strategies = {}

    def test_register_and_get(self):
        class DummyStrategy(Strategy):
            def _default_params(self):
                return {}
            def init(self):
                pass
            def next(self, i):
                return Signal(SignalType.HOLD, "", 0)

        StrategyRegistry.register("dummy", DummyStrategy)
        cls = StrategyRegistry.get("dummy")
        assert cls is DummyStrategy

    def test_get_nonexistent(self):
        assert StrategyRegistry.get("nonexistent") is None

    def test_list_strategies(self):
        class S1(Strategy):
            def _default_params(self):
                return {}
            def init(self):
                pass
            def next(self, i):
                return Signal(SignalType.HOLD, "", 0)

        StrategyRegistry.register("s1", S1)
        StrategyRegistry.register("s2", S1)
        names = StrategyRegistry.list_strategies()
        name_list = [s['name'] for s in names]
        assert "s1" in name_list
        assert "s2" in name_list

    def test_get_info(self):
        class S2(Strategy):
            def _default_params(self):
                return {"period": 14}
            def init(self):
                pass
            def next(self, i):
                return Signal(SignalType.HOLD, "", 0)

            @classmethod
            def get_param_info(cls):
                return [{"name": "period", "type": "int", "default": 14}]

        StrategyRegistry.register("s2", S2)
        info_list = StrategyRegistry.list_strategies()
        s2_info = [s for s in info_list if s['name'] == 's2'][0]
        assert s2_info['name'] == 's2'
        assert len(s2_info['parameters']) == 1
