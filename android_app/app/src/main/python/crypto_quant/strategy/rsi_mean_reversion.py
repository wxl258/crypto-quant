"""
RSI Mean Reversion Strategy
RSI均值回归策略 - 增强版

Improvements:
- Trend filter: EMA 200 direction — only go long above EMA200, only short below EMA200
- RSI divergence detection: price makes higher high but RSI makes lower high = bearish divergence,
  price makes lower low but RSI makes higher low = bullish divergence (enhances entry confidence)
- Partial take-profit: close 50% of position at half the target distance
- Dynamic ATR-based trailing stop: take-profit and stop-loss based on ATR instead of
  relying solely on RSI overbought/oversold exits, which often exit too early.
"""
from typing import Dict, List
import numpy as np
from .base import Strategy, Signal, SignalType

# --- Module-level constants ---
_MIN_DATA_LENGTH = 15
_SIGNAL_QUALITY_THRESHOLD = 0.75
_PARTIAL_TP_TARGET_RATIO_LONG = 1.02
_PARTIAL_TP_TARGET_RATIO_SHORT = 0.98
_DIVERGENCE_PRICE_TOLERANCE_HIGH = 1.001
_DIVERGENCE_PRICE_TOLERANCE_LOW = 0.999
_DEFAULT_RSI_PERIOD = 14
_DEFAULT_ATR_PERIOD = 14
_DEFAULT_ADX_PERIOD = 14
_DEFAULT_OVERSOLD = 30
_DEFAULT_OVERBOUGHT = 70
_DEFAULT_EXIT_MID = 50
_DEFAULT_PARTIAL_TP_RATIO = 0.5
_DEFAULT_ATR_SL_MULT = 1.5
_DEFAULT_ATR_TP_MULT = 2.0
_DEFAULT_ATR_SL_MULT_ENTRY = 1.0
_DEFAULT_ADX_THRESHOLD = 25


class RSIMeanReversionStrategy(Strategy):
    """RSI Mean Reversion with EMA200 trend filter, divergence detection, partial take-profit,
    and dynamic ATR-based trailing stop.

    Entry: RSI < oversold → LONG / RSI > overbought → SHORT
    Exit:  ATR-based TP/SL (primary, when enabled), or
           RSI crosses back through overbought/oversold (secondary), or
           RSI crosses mid-line as safety exit (secondary)
    """

    def _default_params(self):
        return {
            'rsi_period': 10,
            'oversold': 35,
            'overbought': 75,
            'exit_mid': 50,
            'use_mid_exit': True,
            'ema_trend_period': 200,
            'use_trend_filter': True,
            'use_divergence': False,
            'divergence_lookback': 20,
            'partial_tp_ratio': 0.5,
            'use_partial_tp': False,
            'use_atr_exit': True,
            'atr_period': 14,
            'atr_tp_mult': 2.0,
            'atr_sl_mult': 1.0,
            'use_adx_trend_filter': True,
            'adx_trend_threshold': 30,
        }

    def __init__(self, params: Dict = None, **kwargs):
        if params is None:
            params = {}
        if kwargs:
            params = {**params, **kwargs}
        super().__init__(params=params)
        self._partial_tp_triggered: bool = False
        self._tp_price: float = float('inf')
        self._sl_price: float = 0.0
        self._highest_since_entry: float = 0.0

    @classmethod
    def get_param_info(cls) -> List[Dict]:
        return [
            {"name": "rsi_period", "type": "int", "default": 10, "min": 5, "max": 50, "label": "RSI周期"},
            {"name": "oversold", "type": "int", "default": 35, "min": 10, "max": 40, "label": "超卖阈值"},
            {"name": "overbought", "type": "int", "default": 75, "min": 60, "max": 90, "label": "超买阈值"},
            {"name": "exit_mid", "type": "int", "default": 50, "min": 40, "max": 60, "label": "兜底平仓线"},
            {"name": "use_mid_exit", "type": "bool", "default": True, "label": "启用兜底退出"},
            {"name": "ema_trend_period", "type": "int", "default": 200, "min": 50, "max": 300, "label": "EMA趋势周期"},
            {"name": "use_trend_filter", "type": "bool", "default": True, "label": "趋势过滤(EMA200)"},
            {"name": "use_adx_trend_filter", "type": "bool", "default": True, "label": "ADX趋势过滤(暂停均值回归)"},
            {"name": "adx_trend_threshold", "type": "int", "default": 30, "min": 20, "max": 50, "label": "ADX趋势阈值"},
            {"name": "use_divergence", "type": "bool", "default": False, "label": "RSI背离检测"},
            {"name": "divergence_lookback", "type": "int", "default": 20, "min": 5, "max": 50, "label": "背离检测回溯期"},
            {"name": "partial_tp_ratio", "type": "float", "default": 0.5, "min": 0.1, "max": 0.9, "step": 0.05, "label": "部分止盈比例"},
            {"name": "use_partial_tp", "type": "bool", "default": False, "label": "启用部分止盈"},
            {"name": "use_atr_exit", "type": "bool", "default": True, "label": "启用ATR止盈止损"},
            {"name": "atr_period", "type": "int", "default": 14, "min": 5, "max": 50, "label": "ATR周期"},
            {"name": "atr_tp_mult", "type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5, "label": "ATR止盈倍数"},
            {"name": "atr_sl_mult", "type": "float", "default": 1.0, "min": 0.5, "max": 3.0, "step": 0.5, "label": "ATR止损倍数"},
        ]

    def init(self):
        close = self.data['close'].values
        if len(close) < _MIN_DATA_LENGTH:
            self.add_indicator('rsi', np.full(len(close), np.nan))
            self.add_indicator('ema_trend', np.full(len(close), np.nan))
            self.add_indicator('atr', np.full(len(close), np.nan))
            return

        period = self.get_param('rsi_period', _DEFAULT_RSI_PERIOD)
        ema_period = min(self.get_param('ema_trend_period', 200), len(close))
        ema_period = max(ema_period, 2)

        rsi_values = self.rsi(close, period)
        self.add_indicator('rsi', rsi_values)
        self.add_indicator('ema_trend', self.ema(close, ema_period))

        if self.get_param('use_atr_exit', True):
            high = self.data['high'].values
            low = self.data['low'].values
            atr_period = self.get_param('atr_period', _DEFAULT_ATR_PERIOD)
            self.add_indicator('atr', self.atr(high, low, close, atr_period))
        else:
            self.add_indicator('atr', np.full(len(close), np.nan))

    def _detect_divergence(self, i: int) -> str:
        """Detect RSI divergence over lookback window.

        Returns:
            'bullish' - price lower low, RSI higher low (bullish divergence)
            'bearish' - price higher high, RSI lower high (bearish divergence)
            '' - no divergence detected
        """
        if not self.get_param('use_divergence', True):
            return ''

        lookback = self.get_param('divergence_lookback', 20)
        if i < lookback + 1:
            return ''

        rsi = self._indicators['rsi']
        close = self.data['close'].values
        window_start = i - lookback
        mid = window_start + lookback // 2

        rsi_first = rsi[window_start:mid]
        rsi_second = rsi[mid:i+1]

        if np.all(np.isnan(rsi_first)) or np.all(np.isnan(rsi_second)):
            return ''

        first_half_high = np.nanmax(close[window_start:mid])
        first_half_rsi_high = np.nanmax(rsi_first)
        second_half_high = np.nanmax(close[mid:i+1])
        second_half_rsi_high = np.nanmax(rsi_second)
        first_half_low = np.nanmin(close[window_start:mid])
        first_half_rsi_low = np.nanmin(rsi_first)
        second_half_low = np.nanmin(close[mid:i+1])
        second_half_rsi_low = np.nanmin(rsi_second)

        if (second_half_high > first_half_high * _DIVERGENCE_PRICE_TOLERANCE_HIGH and
            second_half_rsi_high < first_half_rsi_high):
            return 'bearish'

        if (second_half_low < first_half_low * _DIVERGENCE_PRICE_TOLERANCE_LOW and
            second_half_rsi_low > first_half_rsi_low):
            return 'bullish'

        return ''

    def _check_atr_exit(self, i: int, price: float, pos: int) -> Signal:
        """Check ATR-based take-profit and stop-loss. Returns exit signal or None."""
        if not self.get_param('use_atr_exit', True):
            return None

        atr = self._indicators.get('atr')
        if atr is None or np.isnan(atr[i]):
            return None

        # Update trailing stop for longs: only trail stop-loss upward
        if pos == 1:
            # Skip if ATR was NaN at entry (tp_price still inf or sl_price still 0)
            if self._tp_price == float('inf') or self._sl_price == 0.0:
                return None

            if price > self._highest_since_entry:
                self._highest_since_entry = price
                current_atr = atr[i]
                # Only update stop-loss; take-profit stays fixed at entry
                self._sl_price = self._highest_since_entry - current_atr * self.get_param('atr_sl_mult', _DEFAULT_ATR_SL_MULT)

            if price >= self._tp_price:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"ATR止盈触发 @ {price:.2f} (TP={self._tp_price:.2f})")
            if price <= self._sl_price:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"ATR止损触发 @ {price:.2f} (SL={self._sl_price:.2f})")

        # Update trailing stop for shorts: only trail stop-loss downward
        elif pos == -1:
            # Skip if ATR was NaN at entry (tp_price still 0.0 or sl_price still inf)
            if self._tp_price == 0.0 or self._sl_price == float('inf'):
                return None

            if self._highest_since_entry == 0 or price < self._highest_since_entry:
                self._highest_since_entry = price
                current_atr = atr[i]
                # Only update stop-loss; take-profit stays fixed at entry
                self._sl_price = self._highest_since_entry + current_atr * self.get_param('atr_sl_mult', _DEFAULT_ATR_SL_MULT)

            if price <= self._tp_price:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"ATR止盈触发 @ {price:.2f} (TP={self._tp_price:.2f})")
            if price >= self._sl_price:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"ATR止损触发 @ {price:.2f} (SL={self._sl_price:.2f})")

        return None

    def next(self, i: int) -> Signal:
        rsi = self._indicators['rsi']
        if np.isnan(rsi[i]):
            return Signal(SignalType.HOLD, "", self.data['close'].iloc[i])

        price = self.data['close'].iloc[i]
        oversold = self.get_param('oversold', _DEFAULT_OVERSOLD)
        overbought = self.get_param('overbought', _DEFAULT_OVERBOUGHT)
        exit_mid = self.get_param('exit_mid', _DEFAULT_EXIT_MID)
        use_mid = self.get_param('use_mid_exit', True)
        use_trend = self.get_param('use_trend_filter', True)
        use_partial = self.get_param('use_partial_tp', True)
        partial_ratio = self.get_param('partial_tp_ratio', _DEFAULT_PARTIAL_TP_RATIO)
        use_atr = self.get_param('use_atr_exit', True)
        pos = self.get_position()

        # ADX trend filter: suspend mean-reversion in trending markets
        if self.get_param('use_adx_trend_filter', True) and pos == 0:
            if self.is_trending_adx(
                self.data['high'].values, self.data['low'].values, self.data['close'].values,
                i, period=_DEFAULT_ADX_PERIOD, threshold=self.get_param('adx_trend_threshold', _DEFAULT_ADX_THRESHOLD)
            ):
                return Signal(SignalType.HOLD, "", price, reason="ADX趋势过滤:暂停均值回归")

        ema_trend = self._indicators['ema_trend']
        price_above_ema = not np.isnan(ema_trend[i]) and price > ema_trend[i]
        price_below_ema = not np.isnan(ema_trend[i]) and price < ema_trend[i]

        divergence = self._detect_divergence(i)

        # --- ATR-based exit (primary) ---
        if pos != 0 and use_atr:
            atr_exit = self._check_atr_exit(i, price, pos)
            if atr_exit is not None:
                return atr_exit

        # --- Exit logic for partial take-profit ---
        if pos == 1 and use_partial and not self._partial_tp_triggered and self._entry_price > 0:
            tp_target = self._entry_price * _PARTIAL_TP_TARGET_RATIO_LONG
            tp_level = self._entry_price + (tp_target - self._entry_price) * partial_ratio
            if price >= tp_level:
                self._partial_tp_triggered = True
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"部分止盈({partial_ratio*100:.0f}%)触发 @ {price:.2f}")

        elif pos == -1 and use_partial and not self._partial_tp_triggered and self._entry_price > 0:
            tp_target = self._entry_price * _PARTIAL_TP_TARGET_RATIO_SHORT
            tp_level = self._entry_price - (self._entry_price - tp_target) * partial_ratio
            if price <= tp_level:
                self._partial_tp_triggered = True
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"部分止盈({partial_ratio*100:.0f}%)触发 @ {price:.2f}")

        # --- Entry logic ---
        if pos == 0:
            if rsi[i] < oversold:
                if use_trend and not price_above_ema:
                    return Signal(SignalType.HOLD, "", price)

                if self.get_param('use_divergence', True) and divergence != 'bullish':
                    return Signal(SignalType.HOLD, "", price)

                # Signal quality filter: only enter on high-quality signals
                if self.signal_quality_score(i, 'BUY') < _SIGNAL_QUALITY_THRESHOLD:
                    return Signal(SignalType.HOLD, "", price, reason="信号质量不足")

                self._entry_price = price
                self._partial_tp_triggered = False
                self._highest_since_entry = price

                # Initialize ATR-based TP/SL for longs
                if use_atr:
                    atr = self._indicators.get('atr')
                    if atr is not None and not np.isnan(atr[i]):
                        current_atr = atr[i]
                        self._tp_price = price + current_atr * self.get_param('atr_tp_mult', _DEFAULT_ATR_TP_MULT)
                        self._sl_price = price - current_atr * self.get_param('atr_sl_mult', _DEFAULT_ATR_SL_MULT_ENTRY)

                reason = f"RSI超卖({rsi[i]:.1f})，做多"
                if divergence == 'bullish':
                    reason += " [看涨背离确认]"
                return Signal(SignalType.BUY, "", price, reason=reason)

            elif rsi[i] > overbought:
                if use_trend and not price_below_ema:
                    return Signal(SignalType.HOLD, "", price)

                if self.get_param('use_divergence', True) and divergence != 'bearish':
                    return Signal(SignalType.HOLD, "", price)

                # Signal quality filter
                if self.signal_quality_score(i, 'SELL') < _SIGNAL_QUALITY_THRESHOLD:
                    return Signal(SignalType.HOLD, "", price, reason="信号质量不足")

                self._entry_price = price
                self._partial_tp_triggered = False
                self._highest_since_entry = price

                # Initialize ATR-based TP/SL for shorts
                if use_atr:
                    atr = self._indicators.get('atr')
                    if atr is not None and not np.isnan(atr[i]):
                        current_atr = atr[i]
                        self._tp_price = price - current_atr * self.get_param('atr_tp_mult', _DEFAULT_ATR_TP_MULT)
                        self._sl_price = price + current_atr * self.get_param('atr_sl_mult', _DEFAULT_ATR_SL_MULT_ENTRY)

                reason = f"RSI超买({rsi[i]:.1f})，做空"
                if divergence == 'bearish':
                    reason += " [看跌背离确认]"
                return Signal(SignalType.SELL, "", price, reason=reason)

        # --- RSI-based exit logic (secondary) ---
        elif pos == 1:
            if rsi[i] > overbought:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"RSI超买({rsi[i]:.1f})，平多止盈")
            if use_mid and rsi[i] < exit_mid:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"RSI跌破中轴({rsi[i]:.1f})，平多止损")
            if use_trend and price_below_ema:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_LONG, "", price,
                              reason=f"价格跌破EMA200趋势反转，平多")

        elif pos == -1:
            if rsi[i] < oversold:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"RSI超卖({rsi[i]:.1f})，平空止盈")
            if use_mid and rsi[i] > exit_mid:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"RSI突破中轴({rsi[i]:.1f})，平空止损")
            if use_trend and price_above_ema:
                self._entry_price = 0.0
                self._partial_tp_triggered = False
                return Signal(SignalType.CLOSE_SHORT, "", price,
                              reason=f"价格突破EMA200趋势反转，平空")

        return Signal(SignalType.HOLD, "", price)
