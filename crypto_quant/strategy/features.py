"""
Feature Engineering Pipeline

Computes a comprehensive feature matrix from OHLCV data for use in
machine learning models, regime detection, and signal generation.

Feature groups (30+ features):
  Price-based:  returns_1h, returns_4h, returns_24h, returns_168h
  Momentum:     ROC(10), ROC(20), ROC(50)
  Volatility:   ATR(14)/close, BB_width, historical_volatility(20)
  Volume:       volume_ratio (vs 20-period avg), volume_trend
  Trend:        ADX(14), ADX_slope, SMA_50/200_ratio
  Oscillators:  RSI(14), Stochastic(14,3), MACD_histogram

All functions handle NaN safely. Uses pandas rolling operations for
vectorized performance.
"""
import numpy as np
import pandas as pd
from typing import Optional


class FeatureEngineer:
    """Compute a full feature matrix from OHLCV data.

    Usage:
        df = FeatureEngineer.compute_features(ohlcv_df)
    """

    @staticmethod
    def compute_features(df: pd.DataFrame) -> pd.DataFrame:
        """Compute all features from OHLCV DataFrame.

        Args:
            df: DataFrame with columns: open, high, low, close, volume (optional)

        Returns:
            DataFrame with all computed features, same index as input.
        """
        if df.empty:
            return pd.DataFrame(index=df.index)

        close = df['close'].astype(float)
        high = df['high'].astype(float) if 'high' in df.columns else close
        low = df['low'].astype(float) if 'low' in df.columns else close
        has_volume = 'volume' in df.columns
        volume = df['volume'].astype(float) if has_volume else None

        features = pd.DataFrame(index=df.index)

        # ── Price-based returns ──
        features['returns_1h'] = close.pct_change(1)
        features['returns_4h'] = close.pct_change(4)
        features['returns_24h'] = close.pct_change(24)
        features['returns_168h'] = close.pct_change(168)

        # ── Momentum: Rate of Change ──
        features['roc_10'] = FeatureEngineer._roc(close, 10)
        features['roc_20'] = FeatureEngineer._roc(close, 20)
        features['roc_50'] = FeatureEngineer._roc(close, 50)

        # ── Volatility ──
        # ATR ratio
        atr = FeatureEngineer._atr(high, low, close, 14)
        features['atr_ratio'] = atr / close.replace(0, np.nan)

        # Bollinger Band width
        bb_width = FeatureEngineer._bb_width(close, 20, 2.0)
        features['bb_width'] = bb_width

        # Historical volatility (20-period annualized)
        features['hist_vol_20'] = FeatureEngineer._historical_volatility(close, 20)

        # ── Volume ──
        if has_volume:
            vol_sma_20 = volume.rolling(window=20, min_periods=1).mean()
            features['volume_ratio'] = volume / vol_sma_20.replace(0, np.nan)
            features['volume_trend'] = vol_sma_20.pct_change(5)
        else:
            features['volume_ratio'] = np.nan
            features['volume_trend'] = np.nan

        # ── Trend ──
        adx, di_plus, di_minus = FeatureEngineer._adx(high, low, close, 14)
        features['adx'] = adx
        features['adx_slope'] = pd.Series(adx, index=df.index).diff(5)

        sma_50 = close.rolling(window=50, min_periods=1).mean()
        sma_200 = close.rolling(window=200, min_periods=1).mean()
        features['sma_50_200_ratio'] = sma_50 / sma_200.replace(0, np.nan)

        # ── Oscillators ──
        features['rsi_14'] = FeatureEngineer._rsi(close, 14)
        features['stoch_k'], features['stoch_d'] = FeatureEngineer._stochastic(high, low, close, 14, 3)
        features['macd_histogram'] = FeatureEngineer._macd_histogram(close)

        return features

    @staticmethod
    def _roc(series: pd.Series, period: int) -> pd.Series:
        """Rate of Change: (price[t] - price[t-period]) / price[t-period]"""
        shifted = series.shift(period)
        shifted = shifted.replace(0, np.nan)
        return (series - shifted) / shifted

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average True Range using Wilder's smoothing."""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        return atr

    @staticmethod
    def _bb_width(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
        """Bollinger Band width: (upper - lower) / middle"""
        middle = close.rolling(window=period, min_periods=period).mean()
        std = close.rolling(window=period, min_periods=period).std()
        upper = middle + std_dev * std
        lower = middle - std_dev * std
        width = (upper - lower) / middle.replace(0, np.nan)
        return width

    @staticmethod
    def _historical_volatility(close: pd.Series, period: int = 20) -> pd.Series:
        """Historical volatility: annualized std of log returns."""
        log_returns = np.log(close / close.shift(1))
        hv = log_returns.rolling(window=period, min_periods=period).std() * np.sqrt(365 * 24)
        return hv

    @staticmethod
    def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
        """Compute ADX, +DI, -DI."""
        up_move = high.diff()
        down_move = -low.diff()

        dm_plus = pd.Series(0.0, index=high.index)
        dm_minus = pd.Series(0.0, index=high.index)
        dm_plus[(up_move > down_move) & (up_move > 0)] = up_move
        dm_minus[(down_move > up_move) & (down_move > 0)] = down_move

        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)

        tr_smooth = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        dm_plus_smooth = dm_plus.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        dm_minus_smooth = dm_minus.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        di_plus = 100.0 * dm_plus_smooth / tr_smooth.replace(0, np.nan)
        di_minus = 100.0 * dm_minus_smooth / tr_smooth.replace(0, np.nan)

        dx = 100.0 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        adx = dx.ewm(alpha=1.0 / period, min_periods=period * 2, adjust=False).mean()

        return adx.values, di_plus.values, di_minus.values

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index using Wilder's smoothing."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                    k_period: int = 14, d_period: int = 3):
        """Stochastic Oscillator: returns (K, D)."""
        lowest_low = low.rolling(window=k_period, min_periods=k_period).min()
        highest_high = high.rolling(window=k_period, min_periods=k_period).max()

        denom = (highest_high - lowest_low).replace(0, np.nan)
        stoch_k = 100.0 * (close - lowest_low) / denom
        stoch_d = stoch_k.rolling(window=d_period, min_periods=d_period).mean()

        return stoch_k, stoch_d

    @staticmethod
    def _macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        """MACD histogram: MACD line - signal line."""
        ema_fast = close.ewm(span=fast, min_periods=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, min_periods=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return histogram
