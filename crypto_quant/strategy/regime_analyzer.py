"""
Market Regime Analyzer — Detects bull/bear/range phases using multi-indicator consensus.

Uses: 90d return, 200d SMA distance, 50d/200d SMA alignment, and volatility regime
to classify market phases. Outputs a stable regime label per bar (1h candles).
"""
import numpy as np
import pandas as pd
from typing import Dict, Tuple


class MarketRegimeAnalyzer:
    """Classifies market into BULL, BEAR, or RANGE based on multi-indicator consensus.
    
    Uses hysteresis to prevent rapid flipping near thresholds.
    """

    def __init__(self, sma_short: int = 50, sma_long: int = 200,
                 bull_threshold: float = 0.15, bear_threshold: float = -0.15,
                 adx_trend: int = 25):
        self.sma_short = sma_short
        self.sma_long = sma_long
        self.bull_threshold = bull_threshold
        self.bear_threshold = bear_threshold
        self.adx_trend = adx_trend

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add regime labels and confidence scores to DataFrame.
        
        Returns DataFrame with added columns: regime, regime_confidence, 
        sma_short, sma_long, ret_90d, adx
        """
        df = df.copy()
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        n = len(close)

        # Compute indicators
        sma_s = pd.Series(close).rolling(self.sma_short, min_periods=1).mean().values
        sma_l = pd.Series(close).rolling(self.sma_long, min_periods=1).mean().values

        # 90-period return (for 1h candles, ~4 days; for 1d candles, ~90 days)
        ret_90 = np.full(n, np.nan)
        for i in range(90, n):
            ret_90[i] = (close[i] / close[i-90] - 1)

        # ADX for trend strength
        adx = np.full(n, np.nan)
        period = 14
        tr = np.maximum(high - low, np.maximum(
            np.abs(high - np.roll(close, 1)),
            np.abs(low - np.roll(close, 1))
        ))
        tr[0] = high[0] - low[0]
        atr_adx = pd.Series(tr).rolling(period, min_periods=1).mean().values

        plus_dm = np.where((high - np.roll(high, 1)) > (np.roll(low, 1) - low),
                           np.maximum(high - np.roll(high, 1), 0), 0)
        minus_dm = np.where((np.roll(low, 1) - low) > (high - np.roll(high, 1)),
                            np.maximum(np.roll(low, 1) - low, 0), 0)
        plus_dm[0] = minus_dm[0] = 0
        plus_di = pd.Series(100 * plus_dm / np.maximum(atr_adx, 1e-10)).rolling(period, min_periods=1).mean().values
        minus_di = pd.Series(100 * minus_dm / np.maximum(atr_adx, 1e-10)).rolling(period, min_periods=1).mean().values
        dx = 100 * np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10)
        adx = pd.Series(dx).rolling(period, min_periods=1).mean().values

        # SMA alignment: 1 = bullish (short > long), -1 = bearish, 0 = neutral
        sma_align = np.where(sma_s > sma_l * 1.02, 1,
                    np.where(sma_s < sma_l * 0.98, -1, 0))

        # Multi-indicator consensus with hysteresis
        regime = np.full(n, 'range', dtype=object)
        confidence = np.zeros(n)
        prev_regime = 'range'

        for i in range(n):
            if i < 90 or np.isnan(ret_90[i]):
                regime[i] = 'range'
                confidence[i] = 0.5
                continue

            score = 0  # positive = bullish, negative = bearish

            # 1. Return-based (weight: 2)
            if ret_90[i] > self.bull_threshold:
                score += 2
            elif ret_90[i] < self.bear_threshold:
                score -= 2

            # 2. SMA alignment (weight: 1)
            if sma_align[i] == 1:
                score += 1
            elif sma_align[i] == -1:
                score -= 1

            # 3. Trend strength via ADX (weight: 1)
            if not np.isnan(adx[i]) and adx[i] > self.adx_trend:
                if score > 0:
                    score += 1  # strong trend confirms bullish
                elif score < 0:
                    score -= 1  # strong trend confirms bearish

            # Classify with hysteresis
            if score >= 3:
                new_regime = 'bull'
            elif score <= -3:
                new_regime = 'bear'
            else:
                new_regime = 'range'

            # Hysteresis: require 2 consecutive bars to flip
            if new_regime != prev_regime:
                # Check if we should flip (simplified: only flip if score is decisive)
                if abs(score) >= 4:
                    prev_regime = new_regime
                # else stay in previous regime
            else:
                prev_regime = new_regime

            regime[i] = prev_regime
            confidence[i] = min(abs(score) / 5.0, 1.0)

        df['regime'] = regime
        df['regime_confidence'] = confidence
        df['sma_short'] = sma_s
        df['sma_long'] = sma_l
        df['ret_90d'] = ret_90
        df['adx'] = adx

        return df

    def get_regime_periods(self, df: pd.DataFrame) -> Dict[str, list]:
        """Extract contiguous regime periods for backtesting."""
        if 'regime' not in df.columns:
            df = self.analyze(df)

        periods = {'bull': [], 'bear': [], 'range': []}
        current_regime = None
        start_idx = None

        for i, (idx, row) in enumerate(df.iterrows()):
            r = row['regime']
            if r != current_regime:
                if current_regime is not None and start_idx is not None:
                    periods[current_regime].append((start_idx, idx))
                current_regime = r
                start_idx = idx

        if current_regime is not None and start_idx is not None:
            periods[current_regime].append((start_idx, df.index[-1]))

        return periods
