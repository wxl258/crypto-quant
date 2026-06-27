"""
AI Trend Predictor — Lightweight ML-based trend classification.

Uses GradientBoosting + feature engineering to predict:
- trend_direction: 1 (up), 0 (flat), -1 (down) over next N bars
- volatility_regime: 'low', 'normal', 'high'
- signal_confidence: 0.0-1.0
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import pickle
import os
from typing import Tuple, Optional


class TrendPredictor:
    """Lightweight ML predictor for crypto trend direction.

    Trains a GradientBoostingClassifier on features computed by
    FeatureEngineer to predict whether price will go up, down, or
    stay flat over the next lookforward bars.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names: list = []
        self.is_trained = False

        if model_path and os.path.exists(model_path):
            self.load(model_path)

    def train(self, df: pd.DataFrame, lookforward: int = 4) -> float:
        """Train the model on OHLCV data.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume (optional)
            lookforward: Number of bars to look ahead for label creation

        Returns:
            Training accuracy (float 0.0-1.0)
        """
        from strategy.features import FeatureEngineer

        if df.empty or len(df) < max(200, lookforward + 1):
            return 0.0

        close = df['close'].values.astype(float)

        # Compute feature matrix
        features = FeatureEngineer.compute_features(df)

        # Create labels: 1 if price goes up by >0.5%, -1 if down by >0.5%, else 0
        future_close = pd.Series(close).shift(-lookforward).values
        current_close_val = close

        labels_arr = np.zeros(len(close), dtype=int)
        for i in range(len(close) - lookforward):
            if future_close[i] > current_close_val[i] * 1.005:
                labels_arr[i] = 1
            elif future_close[i] < current_close_val[i] * 0.995:
                labels_arr[i] = -1
        labels = pd.Series(labels_arr, index=df.index)

        # Align features and labels, drop rows with NaN
        combined = pd.concat([features, labels.rename('label')], axis=1)
        combined = combined.dropna()

        if combined.empty or len(combined) < 50:
            return 0.0

        y = combined['label'].values.astype(int)
        X = combined.drop(columns=['label']).values.astype(float)

        self.feature_names = list(combined.drop(columns=['label']).columns)

        # Scale features
        X_scaled = self.scaler.fit_transform(X)

        # Train GradientBoosting classifier
        self.model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            random_state=42,
        )
        self.model.fit(X_scaled, y)
        self.is_trained = True

        train_accuracy = self.model.score(X_scaled, y)
        return train_accuracy

    def predict(self, df: pd.DataFrame) -> dict:
        """Predict trend direction for the latest bar.

        Args:
            df: OHLCV DataFrame. Prediction is made for the last row.

        Returns:
            dict with keys: direction, confidence, prob_up, prob_down
        """
        from strategy.features import FeatureEngineer

        if not self.is_trained or self.model is None:
            return {
                'direction': 0,
                'confidence': 0.0,
                'prob_up': 0.0,
                'prob_down': 0.0,
            }

        # Compute features for the latest bar
        features = FeatureEngineer.compute_features(df)
        latest = features.iloc[[-1]]

        if latest.isnull().all(axis=1).iloc[0]:
            return {
                'direction': 0,
                'confidence': 0.0,
                'prob_up': 0.0,
                'prob_down': 0.0,
            }

        # Align with training feature names
        X = latest[self.feature_names].values.astype(float)

        # Handle any remaining NaN
        if np.any(np.isnan(X)):
            return {
                'direction': 0,
                'confidence': 0.0,
                'prob_up': 0.0,
                'prob_down': 0.0,
            }

        X_scaled = self.scaler.transform(X)

        # Get probabilities for each class
        proba = self.model.predict_proba(X_scaled)[0]
        classes = self.model.classes_

        prob_up = 0.0
        prob_down = 0.0
        prob_flat = 0.0

        for cls, prob in zip(classes, proba):
            if cls == 1:
                prob_up = prob
            elif cls == -1:
                prob_down = prob
            elif cls == 0:
                prob_flat = prob

        # Determine direction and confidence
        max_prob = max(prob_up, prob_down, prob_flat)
        if max_prob == prob_up and prob_up > prob_flat:
            direction = 1
        elif max_prob == prob_down and prob_down > prob_flat:
            direction = -1
        else:
            direction = 0

        confidence = float(max_prob)

        return {
            'direction': direction,
            'confidence': confidence,
            'prob_up': float(prob_up),
            'prob_down': float(prob_down),
        }

    def save(self, path: str):
        """Save model and scaler to disk."""
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
        data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'is_trained': self.is_trained,
        }
        with open(path, 'wb') as f:
            pickle.dump(data, f)

    def load(self, path: str):
        """Load model and scaler from disk."""
        with open(path, 'rb') as f:
            data = pickle.load(f)

        self.model = data.get('model')
        self.scaler = data.get('scaler', StandardScaler())
        self.feature_names = data.get('feature_names', [])
        self.is_trained = data.get('is_trained', False)

    def get_feature_importance(self) -> list:
        """Return top 10 features by importance.

        Returns:
            List of (feature_name, importance) tuples sorted by importance descending.
        """
        if not self.is_trained or self.model is None:
            return []

        importances = self.model.feature_importances_
        paired = list(zip(self.feature_names, importances))
        paired.sort(key=lambda x: x[1], reverse=True)

        return paired[:10]
