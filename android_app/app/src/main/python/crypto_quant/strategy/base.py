"""
Strategy Base Class and Registry
================================

Defines the core abstractions for the crypto-quant strategy framework:
- `SignalType` and `Signal` -- signal representation for backtest engines
- `Strategy` -- base class for all trading strategies, providing lifecycle hooks,
  position tracking, and built-in technical indicators
- `StrategyRegistry` -- global registry for strategy discovery and parameter introspection
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Enumeration of all supported signal types."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE_LONG = "CLOSE_LONG"
    CLOSE_SHORT = "CLOSE_SHORT"
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"


@dataclass
class Signal:
    """A trading signal emitted by a strategy.

    Attributes:
        signal_type: The type of signal (e.g. BUY, SELL, HOLD).
        symbol: Trading pair symbol (e.g. "BTCUSDT").
        price: Signal price.
        reason: Human-readable reason for the signal.
        timestamp: Optional timestamp (bar index, datetime, etc.).
        quantity: Optional position size override.
        stop_loss: Optional stop-loss price level.
        take_profit: Optional take-profit price level.
    """

    signal_type: SignalType
    symbol: str
    price: float
    reason: str = ""
    timestamp: Any = None
    quantity: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


class Strategy:
    """Base class for all trading strategies.

    Provides lifecycle hooks, position tracking, and built-in technical
    indicators (SMA, EMA, ATR, RSI, Bollinger Bands, MACD).

    Attributes:
        symbol: Trading pair symbol.
        data: OHLCV DataFrame assigned via :meth:`set_data`.
        _params: Arbitrary strategy parameters (settable via constructor
            kwargs or a dict).
        _position: Current position (0=none, 1=long, -1=short).
        _entry_price: Entry price of the current position.
        _entry_bar: Bar index when the last entry occurred (-1 if none).
        _indicators: Dict of precomputed indicators.
    """

    def __init__(self, params: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # 支持 Strategy(params_dict) 和 Strategy(param1=1, param2=2) 两种调用方式
        if params is None:
            params = {}
        if not isinstance(params, dict):
            params = {}
        # kwargs 也合并到 params
        if kwargs:
            params = {**params, **kwargs}
        self.symbol: str = params.pop("symbol", "")
        self.data: Any = params.pop("data", None)
        # Merge _default_params with user-provided params (user overrides defaults)
        defaults = self._default_params()
        self._params: dict[str, Any] = {**defaults, **params}
        # 仓位追踪（用于回测引擎兼容）
        self._position: int = 0
        self._entry_price: float = 0.0
        self._entry_bar: int = -1
        self._indicators: dict[str, Any] = {}

    def get_param(self, name: str, default: Any = None) -> Any:
        """Retrieve a strategy parameter by name.

        Args:
            name: Parameter name.
            default: Value returned when the parameter is not found.

        Returns:
            The parameter value, or *default*.
        """
        return self._params.get(name, default)

    @classmethod
    def get_param_info(cls) -> list[dict[str, Any]]:
        """Return metadata about accepted parameters (for UI/CLI discovery).

        Override in subclasses to advertise parameters.

        Returns:
            A list of dicts, each describing one parameter.
        """
        return []

    def _default_params(self) -> dict[str, Any]:
        """Return the default parameter dict for this strategy.

        Override in subclasses.

        Returns:
            A mapping of parameter name to default value.
        """
        return {}

    # ── 数据与指标方法 ──
    def set_data(self, data: Any) -> None:
        """Set OHLCV data for the strategy.

        Args:
            data: A pandas DataFrame with columns 'open', 'high', 'low', 'close', 'volume'.
        """
        self.data = data

    def add_indicator(self, name: str, value: Any) -> None:
        """Store a precomputed indicator for access during :meth:`next`.

        Args:
            name: Indicator name.
            value: Indicator value (scalar, array, or any object).
        """
        self._indicators[name] = value

    def get_indicator(self, name: str, default: Any = None) -> Any:
        """Get a precomputed indicator by name.

        Args:
            name: Indicator name.
            default: Value returned when the indicator is not found.

        Returns:
            The indicator value, or *default*.
        """
        return self._indicators.get(name, default)

    # ── 生命周期方法 ──
    def init(self) -> None:
        """Initialize strategy -- precompute indicators.

        Called once before the backtest loop starts. Override in subclasses.
        """
        pass

    def next(self, i: int) -> Signal:
        """Called for each bar during the backtest.

        Args:
            i: Current bar index (0-based).

        Returns:
            A :class:`Signal` indicating the desired action.
        """
        return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)

    # ── 仓位管理（回测引擎兼容） ──
    def set_position(self, position: int, entry_price: float) -> None:
        """Track current position from the backtest engine.

        Args:
            position: Position value (0=none, 1=long, -1=short).
            entry_price: Price at which the position was entered.
        """
        self._position = position
        self._entry_price = entry_price

    def get_position(self) -> int:
        """Return current position.

        Returns:
            0 for no position, 1 for long, -1 for short.
        """
        return self._position

    def record_entry(self, i: int) -> None:
        """Record the bar index at which a position was entered.

        Args:
            i: Bar index.
        """
        self._entry_bar = i

    def record_exit(self, i: int) -> None:
        """Record that the position has been exited.

        Resets entry bar and entry price.

        Args:
            i: Bar index (currently unused, reserved for future use).
        """
        self._entry_bar = -1
        self._entry_price = 0.0

    def can_enter(self, i: int) -> bool:
        """Check whether a new entry is permitted at bar *i*.

        Implements a cooldown based on ``min_hold_bars`` parameter.
        Override for custom entry logic.

        Args:
            i: Current bar index.

        Returns:
            ``True`` if entry is allowed.
        """
        min_hold: int = self.get_param('min_hold_bars', 0)
        if min_hold and self._entry_bar >= 0:
            # Simple cooldown: wait min_hold bars after exit
            if i - self._entry_bar < min_hold:
                return False
        return True

    def get_volatility_adjusted_position_pct(self, i: int, base_pct: float) -> float:
        """Return position size fraction adjusted by volatility.

        Base implementation returns *base_pct* unchanged. Override for
        dynamic sizing.

        Args:
            i: Current bar index.
            base_pct: Base position size fraction (e.g. 0.2 for 20%).

        Returns:
            Adjusted position size fraction.
        """
        return base_pct

    def is_trending_adx(self, *args: Any, **kwargs: Any) -> bool:
        """Check if the market is trending at bar *i* using ADX.

        Supports two calling conventions:

        * ``is_trending_adx(i, threshold=25)`` — reads data from ``self.data``.
        * ``is_trending_adx(high, low, close, i, period=14, threshold=25)``
          — uses explicit arrays (backward compatible).

        Args:
            *args: Either ``(i,)`` or ``(high, low, close, i)``.
            threshold: ADX value above which the market is considered trending
                (default 25). Keyword only when using simple signature.
            period: ADX lookback period (default 14). Keyword only.

        Returns:
            ``True`` if ADX exceeds the threshold, ``False`` otherwise or if
            insufficient data.
        """
        # Parse arguments
        if len(args) == 1:
            i = args[0]
            high = self.data['high'].values
            low = self.data['low'].values
            close = self.data['close'].values
            period = kwargs.get('period', self.get_param('adx_period', 14))
            threshold = kwargs.get('threshold', 25.0)
        elif len(args) >= 4:
            high, low, close, i = args[0], args[1], args[2], args[3]
            period = args[4] if len(args) >= 5 else kwargs.get('period', self.get_param('adx_period', 14))
            threshold = args[5] if len(args) >= 6 else kwargs.get('threshold', 25.0)
        else:
            return False

        if i < period * 2:
            return False

        high = np.asarray(high, dtype=float)
        low = np.asarray(low, dtype=float)
        close = np.asarray(close, dtype=float)

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        tr = np.insert(tr, 0, high[0] - low[0])

        dm_plus = np.where(
            (high[1:] - high[:-1]) > (low[:-1] - low[1:]),
            np.maximum(high[1:] - high[:-1], 0),
            0,
        )
        dm_minus = np.where(
            (low[:-1] - low[1:]) > (high[1:] - high[:-1]),
            np.maximum(low[:-1] - low[1:], 0),
            0,
        )
        dm_plus = np.insert(dm_plus, 0, 0)
        dm_minus = np.insert(dm_minus, 0, 0)

        # Wilder's smoothing
        atr = np.full_like(close, np.nan)
        atr[period] = np.mean(tr[:period])
        for j in range(period + 1, i + 1):
            atr[j] = (atr[j - 1] * (period - 1) + tr[j]) / period

        di_plus = np.full_like(close, np.nan)
        di_minus = np.full_like(close, np.nan)
        di_plus[period] = 100 * np.mean(dm_plus[:period]) / atr[period] if atr[period] > 0 else 0
        di_minus[period] = 100 * np.mean(dm_minus[:period]) / atr[period] if atr[period] > 0 else 0
        for j in range(period + 1, i + 1):
            if atr[j] > 0:
                di_plus[j] = (di_plus[j - 1] * (period - 1) + 100 * dm_plus[j] / atr[j]) / period
                di_minus[j] = (di_minus[j - 1] * (period - 1) + 100 * dm_minus[j] / atr[j]) / period

        dx = np.full_like(close, np.nan)
        for j in range(period * 2, i + 1):
            if di_plus[j] + di_minus[j] > 0:
                dx[j] = 100 * abs(di_plus[j] - di_minus[j]) / (di_plus[j] + di_minus[j])

        if np.isnan(dx[i]):
            return False
        return dx[i] > threshold

    def signal_quality_score(self, i: int, signal_type: str, price: float) -> float:
        """Compute a 0-1 quality score for a signal at bar *i*.

        Factors in ADX trend strength, volume ratio, and price position
        relative to Bollinger Bands.

        Args:
            i: Current bar index.
            signal_type: ``'LONG'`` or ``'SHORT'``.
            price: Signal price.

        Returns:
            Quality score between 0 (poor) and 1 (excellent). Returns 0.5
            if insufficient data.
        """
        if i < 30:
            return 0.5

        close = self.data['close'].values
        volume = self.data['volume'].values if 'volume' in self.data.columns else np.ones_like(close)

        score = 0.5

        # Factor 1: ADX trend strength (0.0–0.3 weight)
        period = self.get_param('adx_period', 14)
        if i >= period * 2:
            try:
                is_trend = self.is_trending_adx(i)
                if is_trend:
                    score += 0.15
            except Exception:
                pass

        # Factor 2: Volume confirmation (0.0–0.2 weight)
        if i >= 20:
            vol_ma = np.mean(volume[max(0, i - 19):i + 1])
            if vol_ma > 0 and volume[i] > vol_ma * 1.2:
                score += 0.1

        # Factor 3: BB position (0.0–0.2 weight)
        try:
            mid, upper, lower = self.bollinger_bands(20, 2.0)
            if not np.isnan(mid[i]) and not np.isnan(lower[i]) and not np.isnan(upper[i]):
                bb_range = upper[i] - lower[i]
                if bb_range > 0:
                    if signal_type == 'LONG' and price <= lower[i] * 1.02:
                        score += 0.15
                    elif signal_type == 'SHORT' and price >= upper[i] * 0.98:
                        score += 0.15
        except Exception:
            pass

        return min(1.0, max(0.0, score))

    # ── 技术指标工具 ──
    def sma(self, *args: Any) -> npt.NDArray[np.floating]:
        """Simple Moving Average.

        Supports two calling conventions:

        * ``sma(period)`` -- uses ``self.data['close']``.
        * ``sma(data, period)`` -- uses the provided array.

        Args:
            *args: Either a single ``period`` (int), or ``data`` (array-like)
                and ``period`` (int).

        Returns:
            NumPy array of the same length as the input data. Values before
            the first full window are ``np.nan``.
        """
        if len(args) == 1:
            period = args[0]
            closes: npt.NDArray[np.floating] = self.data['close'].values
        elif len(args) == 2:
            closes, period = args
        else:
            raise TypeError("sma() takes 1 or 2 arguments")
        closes = np.asarray(closes, dtype=float)
        kernel = np.ones(period) / period
        valid = np.convolve(closes, kernel, mode='valid')
        result = np.full(len(closes), np.nan)
        result[period - 1:] = valid
        return result

    def ema(self, *args: Any) -> npt.NDArray[np.floating]:
        """Exponential Moving Average.

        Supports two calling conventions:

        * ``ema(period)`` -- uses ``self.data['close']``.
        * ``ema(data, period)`` -- uses the provided array.

        Args:
            *args: Either a single ``period`` (int), or ``data`` (array-like)
                and ``period`` (int).

        Returns:
            NumPy array of the same length as the input data.
        """
        if len(args) == 1:
            period = args[0]
            closes: npt.NDArray[np.floating] = self.data['close'].values
        elif len(args) == 2:
            closes, period = args
        else:
            raise TypeError("ema() takes 1 or 2 arguments")
        result = np.zeros_like(closes)
        multiplier = 2 / (period + 1)
        result[0] = closes[0]
        for i in range(1, len(closes)):
            result[i] = (closes[i] - result[i-1]) * multiplier + result[i-1]
        return result

    def atr(self, *args: Any) -> npt.NDArray[np.floating]:
        """Average True Range (Wilder's smoothing).

        Supports two calling conventions:

        * ``atr(period)`` -- uses ``self.data`` columns.
        * ``atr(high, low, close, period)`` -- uses provided arrays.

        Args:
            *args: Either a single ``period`` (int), or four positional
                arguments: ``high``, ``low``, ``close``, ``period``.

        Returns:
            NumPy array of the same length as the input data.
        """
        if len(args) == 1:
            period = args[0]
            high: npt.NDArray[np.floating] = self.data['high'].values
            low: npt.NDArray[np.floating] = self.data['low'].values
            close: npt.NDArray[np.floating] = self.data['close'].values
        elif len(args) == 4:
            high, low, close, period = args
        else:
            raise TypeError("atr() takes 1 or 4 arguments")
        tr = np.maximum(high - low,
                       np.maximum(np.abs(high - np.roll(close, 1)),
                                  np.abs(low - np.roll(close, 1))))
        tr[0] = high[0] - low[0]
        result = np.zeros_like(close)
        result[:period] = np.mean(tr[:period])
        for i in range(period, len(close)):
            result[i] = (result[i-1] * (period - 1) + tr[i]) / period
        return result

    def rsi(self, data: npt.NDArray[np.floating] | None = None, period: int = 14) -> npt.NDArray[np.floating]:
        """Relative Strength Index (Wilder's smoothing).

        Supports two calling conventions:
            rsi(period=14)     — use self.data['close']
            rsi(close, 14)     — use explicit close prices

        Args:
            data: Optional close price array. If None, uses self.data['close'].
            period: Lookback period (default 14).

        Returns:
            NumPy array of RSI values. First *period* values are 0.
        """
        if data is None:
            closes: npt.NDArray[np.floating] = self.data['close'].values
        elif isinstance(data, (int, float)):
            # Single-arg call: rsi(period) — interpret scalar as period
            closes = self.data['close'].values
            period = int(data)
        else:
            closes: npt.NDArray[np.floating] = np.asarray(data, dtype=float)
        deltas = np.diff(closes, prepend=closes[0])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        result = np.full_like(closes, np.nan)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(closes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                result[i] = 100.0 if avg_gain > 0 else 50.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - (100.0 / (1.0 + rs))

        return result

    def bollinger_bands(
        self,
        data: npt.NDArray[np.floating] | None = None,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple[npt.NDArray[np.floating], npt.NDArray[np.floating], npt.NDArray[np.floating]]:
        """Bollinger Bands.

        Supports two calling conventions:

        * ``bollinger_bands(period=20, std_dev=2.0)`` — uses ``self.data['close']``.
        * ``bollinger_bands(data, period, std_dev)`` — uses explicit close prices.

        Args:
            data: Optional close price array. If None, uses self.data['close'].
            period: SMA lookback period (default 20).
            std_dev: Number of standard deviations for the bands (default 2.0).

        Returns:
            A tuple of ``(middle, upper, lower)`` NumPy arrays. Values before
            the first full window are ``np.nan``.
        """
        if data is None:
            closes: npt.NDArray[np.floating] = self.data['close'].values
        else:
            closes: npt.NDArray[np.floating] = np.asarray(data, dtype=float)
        closes = np.asarray(closes, dtype=float)
        n = len(closes)
        mid = np.full(n, np.nan, dtype=float)
        upper = np.full(n, np.nan, dtype=float)
        lower = np.full(n, np.nan, dtype=float)

        if n >= period:
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(closes, period)
            mid[period - 1:] = np.mean(windows, axis=1)
            std_arr = np.std(windows, axis=1)
            upper[period - 1:] = mid[period - 1:] + std_dev * std_arr
            lower[period - 1:] = mid[period - 1:] - std_dev * std_arr

        return mid, upper, lower

    def macd(
        self, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[npt.NDArray[np.floating], npt.NDArray[np.floating], npt.NDArray[np.floating]]:
        """MACD (Moving Average Convergence Divergence).

        Args:
            fast: Fast EMA period (default 12).
            slow: Slow EMA period (default 26).
            signal: Signal line EMA period (default 9).

        Returns:
            A tuple of ``(macd_line, signal_line, histogram)`` NumPy arrays.
        """
        fast_ema = self.ema(fast)
        slow_ema = self.ema(slow)
        macd_line = fast_ema - slow_ema
        signal_line = np.zeros_like(macd_line)
        mult = 2 / (signal + 1)
        signal_line[0] = macd_line[0]
        for i in range(1, len(macd_line)):
            signal_line[i] = (macd_line[i] - signal_line[i-1]) * mult + signal_line[i-1]
        return macd_line, signal_line, macd_line - signal_line


class StrategyRegistry:
    """Global registry for strategy discovery and parameter introspection.

    Strategies are registered by name and can be looked up at runtime.
    The registry also supports listing all registered strategies with
    their documentation and parameter metadata.

    Attributes:
        _strategies: Class-level dict mapping strategy names to strategy classes.
        _lazy_modules: Dict mapping strategy names to module import paths for
            lazy loading.
    """

    _strategies: dict[str, type] = {}
    _lazy_modules: dict[str, tuple[str, str]] = {}

    @classmethod
    def register(cls, name: str, strategy_class: type) -> None:
        """Register a strategy class under the given name.

        Args:
            name: Unique strategy name.
            strategy_class: The strategy class (must be a subclass of
                :class:`Strategy`).
        """
        cls._strategies[name] = strategy_class
        logger.debug(f"Registered strategy: {name}")

    @classmethod
    def register_lazy(cls, name: str, module_path: str, class_name: str) -> None:
        """Register a strategy for lazy loading.

        The strategy class will be imported on first access via :meth:`get`.

        Args:
            name: Unique strategy name.
            module_path: Fully-qualified Python module path
                (e.g. ``"strategy.dual_ma"``).
            class_name: Name of the strategy class within the module.
        """
        cls._lazy_modules[name] = (module_path, class_name)

    @classmethod
    def get(cls, name: str) -> type | None:
        """Look up a registered strategy class by name.

        If the strategy is registered via :meth:`register_lazy`, the module
        is imported on first access.

        Args:
            name: Strategy name.

        Returns:
            The strategy class if found, otherwise ``None``.
        """
        if name in cls._strategies:
            return cls._strategies[name]
        if name in cls._lazy_modules:
            import importlib
            module_path, class_name = cls._lazy_modules[name]
            module = importlib.import_module(module_path)
            # Register all lazy entries that point to this module
            for lazy_name, (lazy_path, lazy_class) in cls._lazy_modules.items():
                if lazy_path == module_path and lazy_name not in cls._strategies:
                    obj = getattr(module, lazy_class, None)
                    if obj is not None:
                        cls._strategies[lazy_name] = obj
            if name in cls._strategies:
                return cls._strategies[name]
        return None

    @classmethod
    def list(cls) -> dict[str, type]:
        """Return a shallow copy of the strategy registry.

        Triggers lazy loading of all registered strategies first.

        Returns:
            Dict mapping strategy names to strategy classes.
        """
        # Trigger lazy loading for all registered strategies
        for name in list(cls._lazy_modules.keys()):
            if name not in cls._strategies:
                try:
                    cls.get(name)
                except Exception:
                    pass
        return dict(cls._strategies)

    @classmethod
    def list_strategies(cls) -> list[dict[str, Any]]:
        """Return registered strategies with full metadata.

        Triggers lazy loading of all strategies first.

        Returns:
            A list of dicts, one per registered strategy.
        """
        # Trigger lazy loading for all registered strategies
        for name in list(cls._lazy_modules.keys()):
            if name not in cls._strategies:
                try:
                    cls.get(name)
                except Exception:
                    pass

        result: list[dict[str, Any]] = []
        for name, strategy_cls in cls._strategies.items():
            # Get docstring (first line as short description)
            doc = (strategy_cls.__doc__ or "").strip()
            short_desc = doc.split('\n')[0].strip() if doc else ""
            # Get params
            params: list[dict[str, Any]] = []
            if hasattr(strategy_cls, 'get_param_info'):
                try:
                    params = strategy_cls.get_param_info()
                except Exception as e:
                    logger.debug(f"Failed to get param info for {name}: {e}")
                    pass
            # If no params from get_param_info, try _default_params
            if not params and hasattr(strategy_cls, '_default_params'):
                try:
                    inst = strategy_cls()
                    default_params = inst._default_params() if hasattr(inst, '_default_params') else {}
                    for k, v in default_params.items():
                        params.append({"name": k, "label": k, "default": v, "type": type(v).__name__})
                except Exception as e:
                    logger.debug(f"Failed to get default params for {name}: {e}")
                    pass

            result.append({
                "name": name,
                "class": strategy_cls.__name__,
                "module": strategy_cls.__module__,
                "description": short_desc,
                "parameters": params,
            })
        return result

    @classmethod
    def get_param_info(cls, name: str) -> list[dict[str, Any]]:
        """Get parameter metadata for a specific registered strategy.

        Args:
            name: Strategy name.

        Returns:
            List of parameter descriptors, or an empty list if the strategy
            is not found or does not provide parameter info.
        """
        strategy_cls = cls.get(name)
        if strategy_cls and hasattr(strategy_cls, 'get_param_info'):
            return strategy_cls.get_param_info()
        return []
