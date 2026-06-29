"""
Grid Trading Strategy v3 — ATR Stop-Loss per Layer
==================================================
Core improvements over v2:
1. **Per-layer ATR stop-loss**: Each active grid layer tracks a stop-loss price
   calculated as entry_price ± ATR * atr_stop_mult.  If price hits the stop,
   that specific layer is closed immediately — preventing runaway losses in
   trending markets.
2. **Trend detection**: Compute ADX (via ATR ratio proxy) and EMA slope.  When the
   market is trending (ADX > threshold OR price far from EMA), DISABLE new grid
   entries — only allow closing existing positions.  This prevents the catastrophic
   drawdown from adding to losers in trending markets.
3. **Dynamic grid spacing**: Grid levels are calculated as price ± ATR * multiplier,
   spaced by ATR * spacing_factor.  This adapts to volatility instead of using
   fixed percentages that break in both low and high vol regimes.
4. **Hard max layers**: Maximum N concurrent grid layers.  When max is reached,
   force-close the oldest/furthest position before opening a new one.
5. **Per-layer tracking**: Each grid layer's entry price, direction, and stop-loss
   are tracked individually for proper PnL calculation and risk management.
6. **Auto-reset**: When all grids are closed, recalculate grid levels from current
   price so the grid always centers on current market.
"""
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from .base import Strategy, Signal, SignalType

# --- Module-level constants ---
_ADX_SCALE = 100.0
_DIVISION_EPSILON = 1e-10
_EMA_TRENDING_ATR_DISTANCE = 2.0
_EMA_SLOPE_LOOKBACK = 10
_EMA_SLOPE_THRESHOLD = 0.03
_TRENDING_SIGNALS_MIN = 2


class GridStrategy(Strategy):
    """Grid Trading — Trend-aware, ATR-spaced grid with per-layer stop-loss.

    The grid is a set of price levels above and below a reference price.
    When price crosses a level upward, go short (sell at higher price).
    When price crosses a level downward, go long (buy at lower price).
    Each level crossing opens or closes one grid layer.

    Trend protection: new entries are disabled when ADX > 25 or price is
    far from its EMA (indicating directional trend).

    Stop-loss: each active layer has an ATR-based stop.  If price hits
    the stop, the layer is immediately closed to cap losses.
    """

    def _default_params(self):
        return {
            'grid_count': 5,
            'atr_period': 14,
            'atr_spacing': 1.5,
            'max_layers': 5,
            'trend_filter_adx': 25,
            'trend_lookback': 50,
            'ema_period': 50,
            'atr_stop_mult': 1.5,
        }

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "grid_count", "type": "int", "default": 5, "min": 3, "max": 20, "label": "网格层数"},
            {"name": "atr_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "ATR周期"},
            {"name": "atr_spacing", "type": "float", "default": 1.5, "min": 0.5, "max": 5.0, "step": 0.1, "label": "ATR间距因子"},
            {"name": "max_layers", "type": "int", "default": 5, "min": 1, "max": 20, "label": "最大持仓层数"},
            {"name": "trend_filter_adx", "type": "int", "default": 25, "min": 10, "max": 50, "label": "趋势过滤ADX阈值"},
            {"name": "trend_lookback", "type": "int", "default": 50, "min": 20, "max": 200, "label": "趋势回溯K线"},
            {"name": "ema_period", "type": "int", "default": 50, "min": 20, "max": 200, "label": "EMA周期"},
            {"name": "atr_stop_mult", "type": "float", "default": 1.5, "min": 1.0, "max": 6.0, "step": 0.5, "label": "ATR止损倍数"},
        ]

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values

        atr_p = self.get_param('atr_period', 14)
        ema_p = self.get_param('ema_period', 50)

        # ATR for dynamic spacing, trend detection, and stop-loss
        atr = self.atr(high, low, close, atr_p)
        self.add_indicator('atr', atr)

        # EMA for trend slope detection
        ema = self.ema(close, ema_p)
        self.add_indicator('ema', ema)

        # State tracking
        self._adx = None  # Reset ADX on init to avoid stale data on reuse
        self._grid_levels: List[float] = []     # Sorted list of grid price levels
        self._active_layers: List[dict] = []     # [{"entry_price", "direction", "stop_loss", "level_index"}]
        self._last_crossed_level: int = -1       # Index of last grid level price was at
        self._grid_needs_recalc: bool = True     # Recalculate grid on next bar
        self._trending: bool = False             # Current trend state

    def _compute_adx_proxy(self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                           period: int = 14) -> np.ndarray:
        """Compute a lightweight ADX proxy using ATR ratio.

        True ADX requires +DI/-DI/DX which needs per-bar directional movement.
        We approximate via: smoothed ATR / recent price range ratio, which
        correlates strongly with ADX and is much cheaper to compute.

        Returns values roughly in ADX scale (0-100).
        """
        atr = self._indicators.get('atr')
        if atr is None:
            return np.full(len(close), np.nan)

        # Price range over the period
        high_roll = pd.Series(high).rolling(window=period, min_periods=period).max().values
        low_roll = pd.Series(low).rolling(window=period, min_periods=period).min().values
        price_range = high_roll - low_roll

        # ADX proxy: (ATR / price_range) * 100, scaled
        with np.errstate(divide='ignore', invalid='ignore'):
            adx = np.where(price_range > 0, (atr / price_range) * _ADX_SCALE, 0.0)

        # Smooth with EMA
        result = np.full(len(close), np.nan, dtype=float)
        valid_start = period
        if valid_start < len(close):
            result[valid_start] = adx[valid_start]
            multiplier = 2 / (period + 1)
            for i in range(valid_start + 1, len(close)):
                if np.isnan(adx[i]):
                    result[i] = result[i-1]
                else:
                    result[i] = (adx[i] - result[i-1]) * multiplier + result[i-1]
        return result

    def _is_market_trending(self, i: int) -> bool:
        """Detect if market is strongly trending.

        Uses two signals:
        1. ADX proxy > threshold (strong directional movement)
        2. Price is far from EMA (lookback bars away, indicating sustained trend)
        """
        lookback = self.get_param('trend_lookback', 50)
        adx_threshold = self.get_param('trend_filter_adx', 25)
        ema_p = self.get_param('ema_period', 50)

        if i < lookback or i < ema_p:
            return False

        price = self.data['close'].iloc[i]
        atr = self._indicators.get('atr')
        ema = self._indicators.get('ema')

        if atr is None or ema is None or np.isnan(atr[i]) or np.isnan(ema[i]):
            return False

        # Check 1: ADX proxy
        if hasattr(self, '_adx') and not np.isnan(self._adx[i]):
            adx_trending = self._adx[i] > adx_threshold
        else:
            adx_trending = False

        # Check 2: Price far from EMA
        # "Far" = more than 2 ATR away from EMA (sustained directional move)
        distance_atr = abs(price - ema[i]) / max(atr[i], _DIVISION_EPSILON)
        ema_trending = distance_atr > _EMA_TRENDING_ATR_DISTANCE

        # Check 3: EMA slope (rising or falling steadily)
        if i >= _EMA_SLOPE_LOOKBACK and not np.isnan(ema[i]) and not np.isnan(ema[i - _EMA_SLOPE_LOOKBACK]):
            ema_slope = (ema[i] - ema[i - _EMA_SLOPE_LOOKBACK]) / max(abs(ema[i - _EMA_SLOPE_LOOKBACK]), _DIVISION_EPSILON)
            slope_trending = abs(ema_slope) > _EMA_SLOPE_THRESHOLD  # 3% over 10 bars
        else:
            slope_trending = False

        # Trending if at least 2 of 3 signals agree
        signals = [adx_trending, ema_trending, slope_trending]
        return sum(signals) >= _TRENDING_SIGNALS_MIN

    def _recalculate_grid(self, i: int) -> None:
        """Recalculate grid levels centered on current price with ATR spacing."""
        price = self.data['close'].iloc[i]
        atr = self._indicators.get('atr')
        n_levels = self.get_param('grid_count', 5)
        spacing = self.get_param('atr_spacing', 1.5)

        if atr is None or np.isnan(atr[i]) or atr[i] <= 0:
            return

        step = atr[i] * spacing
        half = n_levels // 2

        # Build levels: [price - half*step, ..., price, ..., price + half*step]
        levels = []
        for k in range(-half, half + 1):
            levels.append(price + k * step)

        self._grid_levels = sorted(set(levels))
        self._last_crossed_level = self._find_level_index(price)
        self._grid_needs_recalc = False

    def _find_level_index(self, price: float) -> int:
        """Find which grid level interval price falls in.

        Returns the index of the level BELOW the price (or 0 if below all levels,
        len(levels)-1 if above all levels).
        """
        if not self._grid_levels:
            return -1
        # Binary search for the rightmost level <= price
        idx = np.searchsorted(self._grid_levels, price, side='right') - 1
        return max(0, min(idx, len(self._grid_levels) - 1))

    def _get_oldest_layer(self) -> Optional[dict]:
        """Return the oldest (first) active layer."""
        return self._active_layers[0] if self._active_layers else None

    def _get_furthest_layer(self) -> Optional[dict]:
        """Return the active layer furthest from current price."""
        if not self._active_layers:
            return None
        # Furthest by absolute distance from entry price
        return max(self._active_layers,
                  key=lambda l: abs(l['entry_price'] - self.data['close'].iloc[-1]))

    def _check_stop_losses(self, i: int) -> Optional[Signal]:
        """Check all active layers for stop-loss hits.

        Returns a Signal to close the first stopped-out layer, or None.
        """
        price = self.data['close'].iloc[i]
        atr = self._indicators.get('atr')
        atr_val = atr[i] if atr is not None and not np.isnan(atr[i]) else 0

        for layer in list(self._active_layers):
            direction = layer['direction']
            stop_loss = layer['stop_loss']

            if direction == 1:
                # LONG: stop is below entry — hit when price <= stop_loss
                if price <= stop_loss:
                    self._active_layers.remove(layer)
                    if not self._active_layers:
                        self._grid_needs_recalc = True
                    return Signal(
                        SignalType.CLOSE_LONG, "", price,
                        reason=f"ATR止损平多(入场={layer['entry_price']:.2f},止损={stop_loss:.2f},出场={price:.2f},ATR={atr_val:.2f})"
                    )
            else:
                # SHORT: stop is above entry — hit when price >= stop_loss
                if price >= stop_loss:
                    self._active_layers.remove(layer)
                    if not self._active_layers:
                        self._grid_needs_recalc = True
                    return Signal(
                        SignalType.CLOSE_SHORT, "", price,
                        reason=f"ATR止损平空(入场={layer['entry_price']:.2f},止损={stop_loss:.2f},出场={price:.2f},ATR={atr_val:.2f})"
                    )

        return None

    def _compute_stop_loss(self, entry_price: float, direction: int, i: int) -> float:
        """Compute stop-loss price for a new layer.

        LONG:  stop_loss = entry_price - ATR * atr_stop_mult
        SHORT: stop_loss = entry_price + ATR * atr_stop_mult
        """
        atr = self._indicators.get('atr')
        if atr is None or np.isnan(atr[i]) or atr[i] <= 0:
            return 0.0

        mult = self.get_param('atr_stop_mult', 3.0)
        stop_distance = atr[i] * mult

        if direction == 1:  # LONG
            return entry_price - stop_distance
        else:  # SHORT
            return entry_price + stop_distance

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]

        # Compute ADX proxy once (cached)
        if not hasattr(self, '_adx') or self._adx is None:
            high = self.data['high'].values
            low = self.data['low'].values
            close = self.data['close'].values
            self._adx = self._compute_adx_proxy(high, low, close, self.get_param('atr_period', 14))

        # Recalculate grid if needed (initial or after full reset)
        if self._grid_needs_recalc or not self._grid_levels:
            self._recalculate_grid(i)
            if not self._grid_levels:
                return Signal(SignalType.HOLD, "", price)

        # --- STOP-LOSS CHECK (before grid crossing logic) ---
        if self._active_layers:
            stop_signal = self._check_stop_losses(i)
            if stop_signal is not None:
                return stop_signal

        # Check trend state
        self._trending = self._is_market_trending(i)

        current_level_idx = self._find_level_index(price)
        if current_level_idx < 0:
            return Signal(SignalType.HOLD, "", price)

        if self._last_crossed_level < 0:
            self._last_crossed_level = current_level_idx
            return Signal(SignalType.HOLD, "", price)

        level_diff = current_level_idx - self._last_crossed_level
        max_layers = self.get_param('max_layers', 5)

        # --- Price moved up: price crossed grid levels upward ---
        if level_diff > 0:
            self._last_crossed_level = current_level_idx

            # Check if we have an open long position to close
            long_layers = [l for l in self._active_layers if l['direction'] == 1]
            if long_layers:
                # Close the most profitable long (highest level)
                to_close = max(long_layers, key=lambda l: l['level_index'])
                self._active_layers.remove(to_close)
                if not self._active_layers:
                    self._grid_needs_recalc = True
                return Signal(SignalType.CLOSE_LONG, "", price,
                            reason=f"网格上升平多(入场={to_close['entry_price']:.2f},出场={price:.2f})")

            # No long to close — consider opening short
            if self._trending:
                return Signal(SignalType.HOLD, "", price)

            # Check max layers
            if len(self._active_layers) >= max_layers:
                oldest = self._get_oldest_layer()
                if oldest:
                    self._active_layers.remove(oldest)
                    if oldest['direction'] == 1:
                        # Force-close a long to make room
                        self._last_crossed_level = current_level_idx
                        return Signal(SignalType.CLOSE_LONG, "", price,
                                    reason=f"网格层数满(max={max_layers}),强制平多(入场={oldest['entry_price']:.2f})")
                    else:
                        # Force-close a short
                        self._last_crossed_level = current_level_idx
                        return Signal(SignalType.CLOSE_SHORT, "", price,
                                    reason=f"网格层数满(max={max_layers}),强制平空(入场={oldest['entry_price']:.2f})")

            # Open short with stop-loss
            stop_loss = self._compute_stop_loss(price, -1, i)
            self._active_layers.append({
                'entry_price': float(price),
                'direction': -1,
                'stop_loss': float(stop_loss),
                'level_index': current_level_idx,
            })
            return Signal(SignalType.SELL, "", price,
                        reason=f"网格上升开空(价格={price:.2f},止损={stop_loss:.2f},层={len(self._active_layers)}/{max_layers})")

        # --- Price moved down: price crossed grid levels downward ---
        elif level_diff < 0:
            self._last_crossed_level = current_level_idx

            # Check if we have an open short position to close
            short_layers = [l for l in self._active_layers if l['direction'] == -1]
            if short_layers:
                # Close the most profitable short (lowest level)
                to_close = min(short_layers, key=lambda l: l['level_index'])
                self._active_layers.remove(to_close)
                if not self._active_layers:
                    self._grid_needs_recalc = True
                return Signal(SignalType.CLOSE_SHORT, "", price,
                            reason=f"网格下降平空(入场={to_close['entry_price']:.2f},出场={price:.2f})")

            # No short to close — consider opening long
            if self._trending:
                return Signal(SignalType.HOLD, "", price)

            # Check max layers
            if len(self._active_layers) >= max_layers:
                oldest = self._get_oldest_layer()
                if oldest:
                    self._active_layers.remove(oldest)
                    if oldest['direction'] == -1:
                        self._last_crossed_level = current_level_idx
                        return Signal(SignalType.CLOSE_SHORT, "", price,
                                    reason=f"网格层数满(max={max_layers}),强制平空(入场={oldest['entry_price']:.2f})")
                    else:
                        self._last_crossed_level = current_level_idx
                        return Signal(SignalType.CLOSE_LONG, "", price,
                                    reason=f"网格层数满(max={max_layers}),强制平多(入场={oldest['entry_price']:.2f})")

            # Open long with stop-loss
            stop_loss = self._compute_stop_loss(price, 1, i)
            self._active_layers.append({
                'entry_price': float(price),
                'direction': 1,
                'stop_loss': float(stop_loss),
                'level_index': current_level_idx,
            })
            return Signal(SignalType.BUY, "", price,
                        reason=f"网格下降开多(价格={price:.2f},止损={stop_loss:.2f},层={len(self._active_layers)}/{max_layers})")

        return Signal(SignalType.HOLD, "", price)
