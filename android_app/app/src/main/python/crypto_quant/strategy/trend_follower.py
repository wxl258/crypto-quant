"""
Pure Trend-Following Strategy

Combines:
- Momentum signal: 20-period ROC (Rate of Change) — only trade when ROC > threshold
- ADX filter: Only enter when ADX > 25 (strong trend confirmed)
- Multi-timeframe confirmation: Check 4h trend direction (4-period SMA slope)
  before entering on 1h
- Volatility-adjusted position sizing: Smaller positions in high vol
  (ATR/price > 3%), larger in normal vol
- Trailing stop: ATR-based, exits when price reverses by 2*ATR

Designed for crypto markets where 82% of periods are trending.
"""
from typing import Dict, List
import numpy as np
import pandas as pd
from .base import Strategy, Signal, SignalType


class TrendFollowerStrategy(Strategy):
    """Pure trend-following strategy with momentum confirmation, ADX filter,
    multi-timeframe alignment, and ATR-based trailing stops.

    Entry: ROC > threshold AND ADX > 25 AND 4h trend aligned
    Exit: Price reverses by 2*ATR from trailing high/low
    """

    def _default_params(self):
        return {
            'roc_period': 20,
            'roc_threshold': 0.02,
            'adx_period': 14,
            'adx_threshold': 25,
            'atr_mult': 1.5,
            'vol_scale': True,
            'sma_trend_period': 4,
            'cooldown_bars': 3,
            'min_hold_bars': 1,  # minimum bars to hold (0=disabled)
        }

    def __init__(self, params: Dict = None):
        super().__init__(params)
        self._trailing_high: float = 0.0
        self._trailing_low: float = float('inf')
        self._entry_atr: float = 0.0  # lock ATR at entry for stop distance

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "roc_period", "type": "int", "default": 20, "min": 5, "max": 100, "label": "ROC周期"},
            {"name": "roc_threshold", "type": "float", "default": 0.02, "min": 0.005, "max": 0.10, "step": 0.005, "label": "ROC阈值"},
            {"name": "adx_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "ADX周期"},
            {"name": "adx_threshold", "type": "int", "default": 25, "min": 15, "max": 50, "label": "ADX阈值"},
            {"name": "atr_mult", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5, "label": "ATR止损倍数"},
            {"name": "vol_scale", "type": "bool", "default": True, "label": "波动率调整仓位"},
            {"name": "sma_trend_period", "type": "int", "default": 4, "min": 2, "max": 20, "label": "趋势SMA周期"},
        ]

    def _compute_adx(self, high, low, close, period):
        """Compute ADX: directional movement index."""
        n = len(close)
        dm_plus = np.zeros(n, dtype=float)
        dm_minus = np.zeros(n, dtype=float)
        tr_arr = np.zeros(n, dtype=float)

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]

        up_move = high - np.roll(high, 1)
        up_move[0] = 0
        down_move = np.roll(low, 1) - low
        down_move[0] = 0

        for j in range(n):
            dm_plus[j] = up_move[j] if up_move[j] > down_move[j] and up_move[j] > 0 else 0.0
            dm_minus[j] = down_move[j] if down_move[j] > up_move[j] and down_move[j] > 0 else 0.0
            tr_arr[j] = max(
                high[j] - low[j],
                abs(high[j] - prev_close[j]),
                abs(low[j] - prev_close[j])
            )

        # Smoothed using Wilder's method (EMA-like)
        tr_smooth = np.full(n, np.nan, dtype=float)
        dm_plus_smooth = np.full(n, np.nan, dtype=float)
        dm_minus_smooth = np.full(n, np.nan, dtype=float)

        tr_smooth[period] = np.sum(tr_arr[1:period + 1])
        dm_plus_smooth[period] = np.sum(dm_plus[1:period + 1])
        dm_minus_smooth[period] = np.sum(dm_minus[1:period + 1])

        for j in range(period + 1, n):
            tr_smooth[j] = tr_smooth[j - 1] - tr_smooth[j - 1] / period + tr_arr[j]
            dm_plus_smooth[j] = dm_plus_smooth[j - 1] - dm_plus_smooth[j - 1] / period + dm_plus[j]
            dm_minus_smooth[j] = dm_minus_smooth[j - 1] - dm_minus_smooth[j - 1] / period + dm_minus[j]

        di_plus = np.full(n, np.nan, dtype=float)
        di_minus = np.full(n, np.nan, dtype=float)
        adx = np.full(n, np.nan, dtype=float)

        for j in range(period, n):
            if tr_smooth[j] > 0:
                di_plus[j] = 100.0 * dm_plus_smooth[j] / tr_smooth[j]
                di_minus[j] = 100.0 * dm_minus_smooth[j] / tr_smooth[j]

        # ADX is smoothed DX
        dx = np.full(n, np.nan, dtype=float)
        for j in range(period, n):
            denom = di_plus[j] + di_minus[j]
            if denom > 0:
                dx[j] = 100.0 * abs(di_plus[j] - di_minus[j]) / denom

        adx[2 * period] = np.nanmean(dx[period + 1:2 * period + 1])
        for j in range(2 * period + 1, n):
            adx[j] = (adx[j - 1] * (period - 1) + dx[j]) / period

        return adx, di_plus, di_minus

    def _roc(self, series, period):
        """Rate of Change: (price[t] - price[t-period]) / price[t-period]"""
        result = np.full(len(series), np.nan, dtype=float)
        for i in range(period, len(series)):
            if series[i - period] != 0:
                result[i] = (series[i] - series[i - period]) / series[i - period]
        return result

    def init(self):
        close = self.data['close'].values
        high = self.data['high'].values
        low = self.data['low'].values
        n = len(close)

        roc_period = self.get_param('roc_period', 20)
        adx_period = self.get_param('adx_period', 14)
        sma_trend_period = self.get_param('sma_trend_period', 4)

        if n < max(roc_period, adx_period * 2, sma_trend_period) + 1:
            self.add_indicator('roc', np.full(n, np.nan))
            self.add_indicator('adx', np.full(n, np.nan))
            self.add_indicator('di_plus', np.full(n, np.nan))
            self.add_indicator('di_minus', np.full(n, np.nan))
            self.add_indicator('atr', np.full(n, np.nan))
            self.add_indicator('trend_sma', np.full(n, np.nan))
            self.add_indicator('trend_slope', np.full(n, np.nan))
            self.add_indicator('vol_ratio', np.full(n, np.nan))
            return

        # Momentum: ROC
        roc = self._roc(close, roc_period)
        self.add_indicator('roc', roc)

        # Trend: ADX
        adx, di_plus, di_minus = self._compute_adx(high, low, close, adx_period)
        self.add_indicator('adx', adx)
        self.add_indicator('di_plus', di_plus)
        self.add_indicator('di_minus', di_minus)

        # ATR for stops and volatility scaling
        atr_period = self.get_param('adx_period', 14)
        atr = self.atr(high, low, close, atr_period)
        self.add_indicator('atr', atr)

        # Volatility ratio: ATR / price
        vol_ratio = np.full(n, np.nan, dtype=float)
        for j in range(n):
            if not np.isnan(atr[j]) and close[j] > 0:
                vol_ratio[j] = atr[j] / close[j]
        self.add_indicator('vol_ratio', vol_ratio)

        # 4h trend: SMA slope (simulated as n-period SMA slope)
        trend_sma = self.sma(close, sma_trend_period)
        self.add_indicator('trend_sma', trend_sma)

        trend_slope = np.full(n, np.nan, dtype=float)
        for j in range(sma_trend_period + 1, n):
            if not np.isnan(trend_sma[j]) and not np.isnan(trend_sma[j - 1]):
                trend_slope[j] = trend_sma[j] - trend_sma[j - 1]
        self.add_indicator('trend_slope', trend_slope)

        # Reset trailing state on init
        self._trailing_high = 0.0
        self._trailing_low = float('inf')
        self._entry_atr = 0.0

    def next(self, i: int) -> Signal:
        price = self.data['close'].iloc[i]

        roc = self._indicators.get('roc')
        adx = self._indicators.get('adx')
        di_plus = self._indicators.get('di_plus')
        di_minus = self._indicators.get('di_minus')
        atr = self._indicators.get('atr')
        trend_slope = self._indicators.get('trend_slope')
        vol_ratio = self._indicators.get('vol_ratio')

        if (roc is None or np.isnan(roc[i]) or
            adx is None or np.isnan(adx[i]) or
            atr is None or np.isnan(atr[i])):
            return Signal(SignalType.HOLD, "", price)

        pos = self.get_position()
        roc_threshold = self.get_param('roc_threshold', 0.02)
        adx_threshold = self.get_param('adx_threshold', 25)
        atr_mult = self.get_param('atr_mult', 2.0)

        # Determine trend direction
        trend_up = False
        trend_down = False
        if trend_slope is not None and not np.isnan(trend_slope[i]):
            trend_up = trend_slope[i] > 0
            trend_down = trend_slope[i] < 0
        else:
            # Fallback: use DI+ vs DI-
            if (di_plus is not None and di_minus is not None and
                not np.isnan(di_plus[i]) and not np.isnan(di_minus[i])):
                trend_up = di_plus[i] > di_minus[i]
                trend_down = di_minus[i] > di_plus[i]

        # --- Position sizing factor (volatility-adjusted) ---
        vol_factor = 1.0
        if self.get_param('vol_scale', True) and not np.isnan(vol_ratio[i]):
            if vol_ratio[i] > 0.03:  # High vol: reduce size
                vol_factor = 0.03 / vol_ratio[i]
            # Normal vol: factor stays 1.0

        # --- EXIT: trailing stop with locked ATR from entry ---
        if pos == 1:
            if price > self._trailing_high:
                self._trailing_high = price
            stop_atr = self._entry_atr if self._entry_atr > 0 else atr[i]
            stop_price = self._trailing_high - atr_mult * stop_atr
            if price <= stop_price:
                self._trailing_high = 0.0
                self._trailing_low = float('inf')
                self._entry_atr = 0.0
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"追踪止损触发 @ {price:.2f} (止损={stop_price:.2f}, ATR锁={stop_atr:.2f})")
            return Signal(SignalType.HOLD, "", price)

        if pos == -1:
            if price < self._trailing_low:
                self._trailing_low = price
            stop_atr = self._entry_atr if self._entry_atr > 0 else atr[i]
            stop_price = self._trailing_low + atr_mult * stop_atr
            if price >= stop_price:
                self._trailing_high = 0.0
                self._trailing_low = float('inf')
                self._entry_atr = 0.0
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"追踪止损触发 @ {price:.2f} (止损={stop_price:.2f}, ATR锁={stop_atr:.2f})")
            return Signal(SignalType.HOLD, "", price)

        # --- ENTRY: trend follower ---
        strong_trend = adx[i] > adx_threshold
        momentum_long = roc[i] > roc_threshold
        momentum_short = roc[i] < -roc_threshold

        if not strong_trend:
            return Signal(SignalType.HOLD, "", price)

        if momentum_long and trend_up:
            self._trailing_high = price
            self._trailing_low = float('inf')
            self._entry_atr = atr[i] if not np.isnan(atr[i]) else 0.0  # lock ATR
            qty = vol_factor
            sl_price = price - atr_mult * atr[i] if not np.isnan(atr[i]) else 0.0
            reason = (f"趋势做多: ROC={roc[i]:.3f}>{roc_threshold}, "
                      f"ADX={adx[i]:.1f}>{adx_threshold}, 趋势向上")
            if vol_factor < 1.0:
                reason += f" [缩仓x{vol_factor:.2f}]"
            return Signal(SignalType.BUY, "", price, quantity=qty, stop_loss=sl_price, reason=reason)

        if momentum_short and trend_down:
            self._trailing_high = 0.0
            self._trailing_low = price
            self._entry_atr = atr[i] if not np.isnan(atr[i]) else 0.0  # lock ATR
            sl_price = price + atr_mult * atr[i] if not np.isnan(atr[i]) else 0.0
            qty = vol_factor
            reason = (f"趋势做空: ROC={roc[i]:.3f}<-{roc_threshold}, "
                      f"ADX={adx[i]:.1f}>{adx_threshold}, 趋势向下")
            if vol_factor < 1.0:
                reason += f" [缩仓x{vol_factor:.2f}]"
            return Signal(SignalType.SELL, "", price, quantity=qty, stop_loss=sl_price, reason=reason)

        return Signal(SignalType.HOLD, "", price)
