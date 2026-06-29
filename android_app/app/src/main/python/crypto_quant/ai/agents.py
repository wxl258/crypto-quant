"""
Multi-Agent Trading System — 4 specialized agents collaborating on trade decisions.

Agents:
- TechnicalAgent: Analyzes OHLCV data, computes indicators, generates technical score
- RiskAgent: Evaluates risk metrics, position sizing, stop-loss levels
- DecisionAgent: Combines inputs from all agents, makes final trade decision
- ReviewAgent: Reviews past trades, provides feedback to improve future decisions
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime


class TechnicalAgent:
    """Analyzes OHLCV data, computes technical indicators, and generates a technical score."""

    def __init__(self, rsi_period: int = 14, bb_period: int = 20, macd_fast: int = 12,
                 macd_slow: int = 26, macd_signal: int = 9, adx_period: int = 14,
                 volume_period: int = 20):
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.adx_period = adx_period
        self.volume_period = volume_period
        self._cached_data_id = None
        self._cached_indicators = {}

    def _compute_all_indicators(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """Precompute all indicators once and cache the results."""
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values if 'volume' in df.columns else None

        indicators = {}
        indicators['rsi'] = self._compute_rsi(close, self.rsi_period)
        macd_line, macd_signal_line, macd_hist = self._compute_macd(close)
        indicators['macd_line'] = macd_line
        indicators['macd_signal_line'] = macd_signal_line
        indicators['macd_hist'] = macd_hist
        bb_mid, bb_upper, bb_lower = self._compute_bb(close)
        indicators['bb_lower'] = bb_lower
        indicators['bb_upper'] = bb_upper
        adx, di_plus, di_minus = self._compute_adx(high, low, close)
        indicators['adx'] = adx
        indicators['di_plus'] = di_plus
        indicators['di_minus'] = di_minus
        if volume is not None:
            indicators['volume_ratio'] = self._compute_volume_ratio(volume)
        else:
            indicators['volume_ratio'] = np.full(len(close), np.nan, dtype=float)
        return indicators

    def analyze(self, df: pd.DataFrame, i: int) -> Dict[str, Any]:
        """Analyze the market at bar index i and return a technical signal.

        Returns:
            dict with keys: signal, confidence, reason, indicators
        """
        data_id = id(df)
        if data_id != self._cached_data_id:
            self._cached_indicators = self._compute_all_indicators(df)
            self._cached_data_id = data_id

        cached = self._cached_indicators
        close = df['close'].values
        volume = df['volume'].values if 'volume' in df.columns else None

        indicators = {}
        score = 0

        # 1. RSI
        rsi_val = cached['rsi'][i]
        indicators['rsi'] = round(float(rsi_val), 2) if not np.isnan(rsi_val) else None

        if not np.isnan(rsi_val):
            if rsi_val < 30:
                score += 1  # oversold — bullish
            elif rsi_val > 70:
                score -= 1  # overbought — bearish

        # 2. MACD
        macd_hist_val = cached['macd_hist'][i]
        indicators['macd_histogram'] = round(float(macd_hist_val), 6) if not np.isnan(macd_hist_val) else None

        if not np.isnan(macd_hist_val):
            if macd_hist_val > 0:
                score += 0.3  # MACD bullish — positive histogram
            else:
                score -= 0.2  # MACD bearish — negative histogram

        # Check MACD histogram momentum shift
        if i >= 1 and not np.isnan(cached['macd_hist'][i]) and not np.isnan(cached['macd_hist'][i - 1]):
            if cached['macd_hist'][i] > cached['macd_hist'][i - 1]:
                score += 0.3  # improving momentum
            elif cached['macd_hist'][i] < cached['macd_hist'][i - 1]:
                score -= 0.2  # deteriorating momentum

        # 3. Bollinger Bands position
        bb_lower_val = cached['bb_lower'][i]
        bb_upper_val = cached['bb_upper'][i]
        indicators['bb_lower'] = round(float(bb_lower_val), 4) if not np.isnan(bb_lower_val) else None
        indicators['bb_upper'] = round(float(bb_upper_val), 4) if not np.isnan(bb_upper_val) else None

        close_i = close[i]
        if not np.isnan(bb_lower_val) and close_i <= bb_lower_val * 1.01:
            score += 1  # near/lower than lower band — bullish reversal signal
        if not np.isnan(bb_upper_val) and close_i >= bb_upper_val * 0.99:
            score -= 1  # near/above upper band — bearish reversal signal

        # 4. ADX (trend strength)
        adx_val = cached['adx'][i]
        indicators['adx'] = round(float(adx_val), 2) if not np.isnan(adx_val) else None

        if not np.isnan(adx_val) and adx_val > 25:
            score += 1  # trending market — good for directional trades

        # 5. Volume ratio
        if volume is not None:
            vol_ratio_val = cached['volume_ratio'][i]
            indicators['volume_ratio'] = round(float(vol_ratio_val), 2) if not np.isnan(vol_ratio_val) else None

            if not np.isnan(vol_ratio_val) and vol_ratio_val > 1.5:
                score += 1  # volume surge — confirms moves
        else:
            indicators['volume_ratio'] = None

        # Determine signal based on score
        if score >= 2:
            signal = 'BUY'
            confidence = min(0.5 + score * 0.15, 1.0)
            reason = self._build_reason(score, indicators)
        elif score <= -2:
            signal = 'SELL'
            confidence = min(0.5 + abs(score) * 0.15, 1.0)
            reason = self._build_reason(score, indicators)
        else:
            signal = 'HOLD'
            confidence = 0.5 - abs(score) * 0.1
            reason = f"Insufficient signals (score={score})"

        return {
            'signal': signal,
            'confidence': confidence,
            'reason': reason,
            'indicators': indicators,
            'score': score,
        }

    def _build_reason(self, score: int, indicators: Dict) -> str:
        parts = []
        rsi = indicators.get('rsi')
        if rsi is not None:
            if rsi < 30:
                parts.append(f"RSI oversold({rsi:.1f})")
            elif rsi > 70:
                parts.append(f"RSI overbought({rsi:.1f})")
        vol = indicators.get('volume_ratio')
        if vol is not None and vol > 1.5:
            parts.append(f"Vol surge({vol:.1f}x)")
        adx = indicators.get('adx')
        if adx is not None and adx > 25:
            parts.append(f"Trending(ADX={adx:.1f})")
        return f"Score={score}: " + ", ".join(parts) if parts else f"Score={score}"

    def _compute_rsi(self, close: np.ndarray, period: int) -> np.ndarray:
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)

        result = np.full(len(close), np.nan, dtype=float)
        if len(close) <= period:
            return result

        avg_gain = np.mean(gain[1:period + 1])
        avg_loss = np.mean(loss[1:period + 1])
        if avg_loss == 0:
            result[period] = 100.0
        else:
            result[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

        for i in range(period + 1, len(close)):
            avg_gain = (avg_gain * (period - 1) + gain[i]) / period
            avg_loss = (avg_loss * (period - 1) + loss[i]) / period
            if avg_loss == 0:
                result[i] = 100.0
            else:
                result[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        return result

    def _compute_macd(self, close: np.ndarray):
        ema_fast = self._ema(close, self.macd_fast)
        ema_slow = self._ema(close, self.macd_slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._ema(macd_line, self.macd_signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def _ema(self, series: np.ndarray, period: int) -> np.ndarray:
        result = np.full(len(series), np.nan, dtype=float)
        if len(series) < period:
            return result
        series_f = series.astype(float)
        start = 0
        while start < len(series_f) and np.isnan(series_f[start]):
            start += 1
        if start + period > len(series_f):
            return result
        result[start + period - 1] = np.mean(series_f[start:start + period])
        multiplier = 2.0 / (period + 1)
        for i in range(start + period, len(series_f)):
            if np.isnan(series_f[i]):
                result[i] = result[i - 1]
            else:
                result[i] = (series_f[i] - result[i - 1]) * multiplier + result[i - 1]
        return result

    def _compute_bb(self, close: np.ndarray):
        period = self.bb_period
        mid = np.full(len(close), np.nan, dtype=float)
        upper = np.full(len(close), np.nan, dtype=float)
        lower = np.full(len(close), np.nan, dtype=float)
        if len(close) < period:
            return mid, upper, lower
        for i in range(period - 1, len(close)):
            window = close[i - period + 1:i + 1]
            m = np.mean(window)
            s = np.std(window, ddof=0)
            mid[i] = m
            upper[i] = m + 2.0 * s
            lower[i] = m - 2.0 * s
        return mid, upper, lower

    def _compute_adx(self, high: np.ndarray, low: np.ndarray, close: np.ndarray):
        period = self.adx_period
        n = len(close)
        adx = np.full(n, np.nan, dtype=float)
        di_plus = np.full(n, np.nan, dtype=float)
        di_minus = np.full(n, np.nan, dtype=float)
        if n < period * 2:
            return adx, di_plus, di_minus

        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        up_move = np.maximum(high[1:] - high[:-1], 0.0)
        down_move = np.maximum(low[:-1] - low[1:], 0.0)

        dm_plus = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr_smooth = np.zeros(n - 1)
        dm_plus_smooth = np.zeros(n - 1)
        dm_minus_smooth = np.zeros(n - 1)

        tr_smooth[period - 1] = np.sum(tr[:period])
        dm_plus_smooth[period - 1] = np.sum(dm_plus[:period])
        dm_minus_smooth[period - 1] = np.sum(dm_minus[:period])

        for j in range(period, n - 1):
            tr_smooth[j] = tr_smooth[j - 1] - tr_smooth[j - 1] / period + tr[j]
            dm_plus_smooth[j] = dm_plus_smooth[j - 1] - dm_plus_smooth[j - 1] / period + dm_plus[j]
            dm_minus_smooth[j] = dm_minus_smooth[j - 1] - dm_minus_smooth[j - 1] / period + dm_minus[j]

        for j in range(period - 1, n - 1):
            denom = tr_smooth[j]
            if denom == 0:
                continue
            dp = 100.0 * dm_plus_smooth[j] / denom
            dm = 100.0 * dm_minus_smooth[j] / denom
            di_plus[j + 1] = dp
            di_minus[j + 1] = dm
            if dp + dm > 0:
                dx = 100.0 * abs(dp - dm) / (dp + dm)
                if j + 1 >= period * 2 - 1:
                    if np.isnan(adx[j]):
                        adx[j + 1] = dx
                    else:
                        adx[j + 1] = (adx[j] * (period - 1) + dx) / period

        return adx, di_plus, di_minus

    def _compute_volume_ratio(self, volume: np.ndarray) -> np.ndarray:
        period = self.volume_period
        result = np.full(len(volume), np.nan, dtype=float)
        if len(volume) < period:
            return result
        vol_sma = np.full(len(volume), np.nan, dtype=float)
        cumsum = np.cumsum(np.insert(volume.astype(float), 0, 0))
        vol_sma[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
        for i in range(period - 1, len(volume)):
            if vol_sma[i] > 0:
                result[i] = volume[i] / vol_sma[i]
        return result


class RiskAgent:
    """Evaluates risk metrics, position sizing, and stop-loss levels."""

    def __init__(self, max_position_pct: float = 0.2, daily_loss_limit: float = 0.05,
                 max_positions: int = 3, atr_period: int = 14, atr_stop_mult: float = 2.0,
                 risk_reward_ratio: float = 2.0):
        self.max_position_pct = max_position_pct
        self.daily_loss_limit = daily_loss_limit
        self.max_positions = max_positions
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.risk_reward_ratio = risk_reward_ratio

    def evaluate(self, account: Dict[str, Any], signal: str, price: float) -> Dict[str, Any]:
        """Evaluate risk for a proposed trade.

        Args:
            account: dict with keys: capital, positions, daily_pnl, daily_loss (optional)
            signal: 'BUY', 'SELL', or 'HOLD'
            price: current price

        Returns:
            dict with keys: approved, max_size, stop_loss, take_profit, reason
        """
        if signal == 'HOLD':
            return {
                'approved': True,
                'max_size': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'reason': 'No action needed (HOLD signal)',
            }

        capital = account.get('capital', 10000.0)
        positions = account.get('positions', 0)
        daily_pnl = account.get('daily_pnl', 0.0)
        daily_loss = account.get('daily_loss', 0.0)
        atr = account.get('atr', price * 0.01)

        reasons = []

        # Check daily loss limit
        if capital > 0 and daily_loss / capital > self.daily_loss_limit:
            return {
                'approved': False,
                'max_size': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'reason': f'Daily loss limit exceeded ({daily_loss/capital*100:.1f}% > {self.daily_loss_limit*100:.0f}%)',
            }

        # Check max positions
        if positions >= self.max_positions:
            return {
                'approved': False,
                'max_size': 0.0,
                'stop_loss': 0.0,
                'take_profit': 0.0,
                'reason': f'Max positions reached ({positions}/{self.max_positions})',
            }

        # Position sizing
        max_position_value = capital * self.max_position_pct
        max_size = max_position_value / price if price > 0 else 0.0

        # ATR-based stop loss
        if signal == 'BUY':
            stop_loss = price - self.atr_stop_mult * atr
            take_profit = price + self.atr_stop_mult * self.risk_reward_ratio * atr
        else:  # SELL
            stop_loss = price + self.atr_stop_mult * atr
            take_profit = price - self.atr_stop_mult * self.risk_reward_ratio * atr

        # Risk per trade check (1% of capital)
        risk_amount = abs(price - stop_loss) * max_size if max_size > 0 else 0
        if risk_amount > capital * 0.01:
            max_size = (capital * 0.01) / abs(price - stop_loss) if abs(price - stop_loss) > 0 else max_size
            reasons.append('Size reduced for 1% risk limit')

        reasons.append(f'ATR stop at {stop_loss:.4f}')

        return {
            'approved': True,
            'max_size': round(max_size, 8),
            'stop_loss': round(stop_loss, 4),
            'take_profit': round(take_profit, 4),
            'reason': '; '.join(reasons) if reasons else 'Risk check passed',
        }

    def compute_atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, i: int) -> float:
        """Compute ATR at a given index."""
        if i < self.atr_period:
            return np.nan
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close))
        )
        atr_vals = np.full(len(close), np.nan, dtype=float)
        atr_vals[self.atr_period] = np.mean(tr[1:self.atr_period + 1])
        for j in range(self.atr_period + 1, len(close)):
            atr_vals[j] = (atr_vals[j - 1] * (self.atr_period - 1) + tr[j]) / self.atr_period
        return float(atr_vals[i])


class DecisionAgent:
    """Combines inputs from all agents and makes the final trade decision."""

    def __init__(self, tech_weight: float = 0.5, risk_weight: float = 0.3,
                 sentiment_weight: float = 0.2, min_confidence: float = 0.6):
        self.tech_weight = tech_weight
        self.risk_weight = risk_weight
        self.sentiment_weight = sentiment_weight
        self.min_confidence = min_confidence

    def decide(self, technical_result: Dict[str, Any], risk_result: Dict[str, Any],
               sentiment_score: float = 0.5) -> Dict[str, Any]:
        """Make a final trade decision using weighted voting.

        Args:
            technical_result: output from TechnicalAgent.analyze()
            risk_result: output from RiskAgent.evaluate()
            sentiment_score: external sentiment score (0-1, 0.5 = neutral)

        Returns:
            dict with keys: action, size, sl, tp, confidence
        """
        # Technical signal mapping to score
        tech_signal = technical_result.get('signal', 'HOLD')
        tech_conf = technical_result.get('confidence', 0.5)

        if tech_signal == 'BUY':
            tech_score = tech_conf
        elif tech_signal == 'SELL':
            tech_score = -tech_conf
        else:
            tech_score = 0.0

        # Risk score
        risk_approved = risk_result.get('approved', False)
        risk_score = 1.0 if risk_approved else -1.0

        # Sentiment score: map 0-1 to -1 to 1
        sent_score = (sentiment_score - 0.5) * 2.0

        # Weighted combined score
        combined = (
            self.tech_weight * tech_score +
            self.risk_weight * risk_score +
            self.sentiment_weight * sent_score
        )

        confidence = abs(combined)

        if not risk_approved:
            return {
                'action': 'HOLD',
                'size': 0.0,
                'sl': 0.0,
                'tp': 0.0,
                'confidence': confidence,
                'reason': 'Risk check failed',
            }

        if confidence < self.min_confidence:
            return {
                'action': 'HOLD',
                'size': 0.0,
                'sl': 0.0,
                'tp': 0.0,
                'confidence': confidence,
                'reason': f'Confidence below threshold ({confidence:.2f} < {self.min_confidence})',
            }

        if combined > 0:
            action = 'BUY'
        elif combined < 0:
            action = 'SELL'
        else:
            action = 'HOLD'

        size = risk_result.get('max_size', 0.0)
        sl = risk_result.get('stop_loss', 0.0)
        tp = risk_result.get('take_profit', 0.0)

        return {
            'action': action,
            'size': size,
            'sl': sl,
            'tp': tp,
            'confidence': confidence,
            'reason': technical_result.get('reason', ''),
        }


class ReviewAgent:
    """Reviews past trades and provides feedback to improve future decisions."""

    def __init__(self):
        self.trades: List[Dict] = []
        self.feedback: List[str] = []

    def review(self, trades_history: List[Dict]) -> Dict[str, Any]:
        """Analyze past trades and compute performance metrics.

        Args:
            trades_history: list of trade dicts with keys:
                signal_type, entry_price, exit_price, pnl, pnl_pct, entry_time, exit_time

        Returns:
            dict with analysis results
        """
        self.trades = trades_history

        if not trades_history:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'best_signal': None,
                'worst_signal': None,
                'optimal_hold_hours': 0,
                'best_entry_hour': None,
            }

        total = len(trades_history)
        winners = [t for t in trades_history if t.get('pnl', 0) > 0]
        losers = [t for t in trades_history if t.get('pnl', 0) <= 0]

        win_rate = len(winners) / total if total > 0 else 0.0

        gross_profit = sum(t.get('pnl', 0) for t in winners)
        gross_loss = abs(sum(t.get('pnl', 0) for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Win rate by signal type
        signal_stats: Dict[str, Dict] = {}
        for t in trades_history:
            sig = t.get('signal_type', 'UNKNOWN')
            if sig not in signal_stats:
                signal_stats[sig] = {'wins': 0, 'total': 0, 'total_pnl': 0.0}
            signal_stats[sig]['total'] += 1
            signal_stats[sig]['total_pnl'] += t.get('pnl', 0)
            if t.get('pnl', 0) > 0:
                signal_stats[sig]['wins'] += 1

        for sig in signal_stats:
            s = signal_stats[sig]
            s['win_rate'] = s['wins'] / s['total'] if s['total'] > 0 else 0.0

        # Best and worst signal types by win rate
        ranked = sorted(signal_stats.items(), key=lambda x: x[1]['win_rate'], reverse=True)
        best_signal = ranked[0][0] if ranked else None
        worst_signal = ranked[-1][0] if ranked else None

        # Optimal holding time
        hold_times = []
        for t in trades_history:
            entry = t.get('entry_time')
            exit_t = t.get('exit_time')
            if entry is not None and exit_t is not None:
                if isinstance(entry, (int, float)):
                    hold_times.append(exit_t - entry)
                elif hasattr(entry, 'timestamp'):
                    hold_times.append(exit_t - entry)
        optimal_hold = np.mean(hold_times) if hold_times else 0

        # Best entry hour
        hour_pnl: Dict[int, list] = {}
        for t in trades_history:
            et = t.get('entry_time')
            if et is not None:
                if isinstance(et, datetime):
                    hour = et.hour
                elif isinstance(et, pd.Timestamp):
                    hour = et.hour
                else:
                    continue
                if hour not in hour_pnl:
                    hour_pnl[hour] = []
                hour_pnl[hour].append(t.get('pnl', 0))
        best_entry_hour = None
        if hour_pnl:
            best_entry_hour = max(hour_pnl, key=lambda h: np.mean(hour_pnl[h]) if hour_pnl[h] else 0)

        analysis = {
            'total_trades': total,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'best_signal': best_signal,
            'worst_signal': worst_signal,
            'optimal_hold_hours': optimal_hold,
            'best_entry_hour': best_entry_hour,
            'signal_stats': signal_stats,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
        }

        self._generate_feedback(analysis)
        return analysis

    def _generate_feedback(self, analysis: Dict):
        """Generate improvement suggestions based on trade analysis."""
        self.feedback = []

        if analysis['total_trades'] < 5:
            self.feedback.append("Not enough trades for reliable analysis — collect more data.")
            return

        if analysis['win_rate'] < 0.4:
            self.feedback.append(f"Low win rate ({analysis['win_rate']:.1%}). Consider increasing confidence threshold or reducing trade frequency.")

        if analysis['profit_factor'] < 1.0:
            self.feedback.append(f"Profit factor below 1.0 ({analysis['profit_factor']:.2f}). Cut losses faster or let winners run longer.")

        if analysis['profit_factor'] > 2.0:
            self.feedback.append("Strong profit factor. Consider increasing position size cautiously.")

        best_sig = analysis.get('best_signal')
        worst_sig = analysis.get('worst_signal')
        if best_sig and worst_sig and best_sig != worst_sig:
            self.feedback.append(f"'{best_sig}' signals outperform '{worst_sig}' — weight '{best_sig}' signals higher or filter '{worst_sig}'.")

        signal_stats = analysis.get('signal_stats', {})
        for sig, stats in signal_stats.items():
            if stats['total'] >= 3 and stats['win_rate'] < 0.3:
                self.feedback.append(f"Consider disabling '{sig}' signals — win rate only {stats['win_rate']:.1%} over {stats['total']} trades.")

        if analysis.get('best_entry_hour') is not None:
            self.feedback.append(f"Best entry hour: {analysis['best_entry_hour']}:00 UTC — prioritize trades during this window.")

    def get_feedback(self) -> List[str]:
        """Return improvement suggestions based on trade analysis."""
        return self.feedback
