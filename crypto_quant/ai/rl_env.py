"""
Reinforcement Learning Trading Environment — OpenAI Gym compatible.

State space: [price, RSI, MACD, BB_position, volume_ratio, position, pnl_pct] (7 dims)
Action space: Discrete(3) — 0=HOLD, 1=BUY, 2=SELL
Reward: PnL change + Sharpe-like penalty for drawdowns
"""
import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional
from strategy.features import FeatureEngineer


class TradingEnv:
    """A Gym-compatible environment for training RL trading agents.

    State space (7 dimensions):
        0: price (normalized by initial price)
        1: RSI (0-100)
        2: MACD histogram
        3: BB position (0-1, where 0=lower band, 1=upper band)
        4: volume ratio
        5: position (-1, 0, 1)
        6: pnl_pct (unrealized PnL %)

    Action space: Discrete(3)
        0: HOLD
        1: BUY (or close short and go long)
        2: SELL (or close long and go short)
    """

    def __init__(self, df: pd.DataFrame, initial_capital: float = 10000.0,
                 commission: float = 0.0004, leverage: int = 3,
                 window_size: int = 50):
        """Initialize the trading environment.

        Args:
            df: OHLCV DataFrame with columns: open, high, low, close, volume
            initial_capital: starting capital
            commission: trading fee as fraction (e.g., 0.0004 = 0.04%)
            leverage: leverage multiplier
            window_size: minimum bars needed before trading starts
        """
        if df.empty:
            raise ValueError("DataFrame must not be empty")

        self.df = df.copy()
        self.initial_capital = initial_capital
        self.commission = commission
        self.leverage = leverage
        self.window_size = window_size

        # Pre-compute features for state construction
        self._compute_features()

        # Environment state
        self.current_step: int = 0
        self.capital: float = initial_capital
        self.position: int = 0  # 0=none, 1=long, -1=short
        self.entry_price: float = 0.0
        self.equity_curve: list = []
        self.prev_equity: float = initial_capital
        self.max_equity: float = initial_capital
        self.total_steps: int = 0

        self.action_space = type('ActionSpace', (), {'n': 3})()
        self.observation_space = type('ObservationSpace', (), {
            'shape': (7,),
            'low': np.array([0, 0, -np.inf, 0, 0, -1, -np.inf]),
            'high': np.array([np.inf, 100, np.inf, 1, np.inf, 1, np.inf]),
        })()

    def _compute_features(self):
        """Pre-compute all features needed for state construction."""
        self.features = FeatureEngineer.compute_features(self.df)

        # Extract arrays for fast access
        self.close = self.df['close'].astype(float).values
        self.high = self.df['high'].astype(float).values if 'high' in self.df.columns else self.close
        self.low = self.df['low'].astype(float).values if 'low' in self.df.columns else self.close
        self.volume = self.df['volume'].astype(float).values if 'volume' in self.df.columns else None

        self.rsi_vals = self.features['rsi_14'].values if 'rsi_14' in self.features.columns else np.full(len(self.df), np.nan)
        self.macd_vals = self.features['macd_histogram'].values if 'macd_histogram' in self.features.columns else np.full(len(self.df), np.nan)

        # BB position
        bb_width = self.features['bb_width'].values if 'bb_width' in self.features.columns else np.full(len(self.df), np.nan)
        self.bb_width = bb_width

        self.vol_ratio = self.features['volume_ratio'].values if 'volume_ratio' in self.features.columns else np.full(len(self.df), np.nan)

        self.initial_price = self.close[self.window_size] if len(self.close) > self.window_size else self.close[0]
        self.n_bars = len(self.df)

    def reset(self) -> np.ndarray:
        """Reset the environment to initial state.

        Returns:
            Initial state observation (7-dim numpy array).
        """
        self.current_step = self.window_size
        self.capital = self.initial_capital
        self.position = 0
        self.entry_price = 0.0
        self.equity_curve = [self.initial_capital]
        self.prev_equity = self.initial_capital
        self.max_equity = self.initial_capital
        self.total_steps = 0

        return self._get_state()

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict]:
        """Execute one step in the environment.

        Args:
            action: 0=HOLD, 1=BUY, 2=SELL

        Returns:
            (next_state, reward, done, info)
        """
        if action not in (0, 1, 2):
            raise ValueError(f"Invalid action: {action}. Must be 0, 1, or 2.")

        prev_capital = self.capital
        prev_equity = self._compute_equity()

        # Get current price
        price = self.close[self.current_step]
        high = self.high[self.current_step]
        low = self.low[self.current_step]

        # Execute action
        if action == 1:  # BUY
            if self.position == -1:
                # Close short position
                pnl = (self.entry_price - price) * self._position_size() * self.leverage
                self.capital += pnl - self.commission * self._position_size() * self.leverage * self.entry_price
                self.position = 0
                self.entry_price = 0.0

            if self.position == 0:
                # Open long position
                self.entry_price = price
                self.position = 1
                # Deduct commission
                pos_size = self._position_size()
                self.capital -= self.commission * pos_size * price * self.leverage

        elif action == 2:  # SELL
            if self.position == 1:
                # Close long position
                pnl = (price - self.entry_price) * self._position_size() * self.leverage
                self.capital += pnl - self.commission * self._position_size() * self.leverage * self.entry_price
                self.position = 0
                self.entry_price = 0.0

            if self.position == 0:
                # Open short position
                self.entry_price = price
                self.position = -1
                pos_size = self._position_size()
                self.capital -= self.commission * pos_size * price * self.leverage

        # action == 0: HOLD — do nothing

        # Advance to next bar
        self.current_step += 1
        self.total_steps += 1

        # Compute current equity
        current_equity = self._compute_equity()
        self.equity_curve.append(current_equity)

        # Track max equity for drawdown calculation
        if current_equity > self.max_equity:
            self.max_equity = current_equity

        # Compute reward
        reward = self._compute_reward(prev_equity, current_equity)

        # Check if done
        done = self._is_done()

        # Prepare info dict
        info = {
            'step': self.current_step,
            'capital': self.capital,
            'equity': current_equity,
            'position': self.position,
            'entry_price': self.entry_price,
            'price': price,
        }

        # Update prev_equity for next step
        self.prev_equity = current_equity

        # Get next state
        if not done:
            next_state = self._get_state()
        else:
            next_state = np.zeros(7, dtype=np.float32)

        return next_state, reward, done, info

    def _compute_equity(self) -> float:
        """Compute current equity including unrealized PnL."""
        if self.position == 0 or self.current_step >= self.n_bars:
            return self.capital

        price = self.close[self.current_step]
        pos_size = self._position_size()

        if self.position == 1:  # Long
            unrealized = (price - self.entry_price) * pos_size * self.leverage
        else:  # Short
            unrealized = (self.entry_price - price) * pos_size * self.leverage

        return self.capital + unrealized

    def _position_size(self) -> float:
        """Compute position size in base units."""
        if self.initial_price <= 0:
            return 0.0
        return self.capital / self.initial_price

    def _compute_reward(self, prev_equity: float, current_equity: float) -> float:
        """Compute reward based on PnL change with drawdown penalty.

        Reward = (current_equity - prev_equity) / prev_equity * 100
                 - 0.01 * drawdown_penalty
        """
        if prev_equity <= 0:
            return 0.0

        pnl_return = (current_equity - prev_equity) / prev_equity * 100.0

        # Drawdown penalty
        if self.max_equity > 0:
            drawdown = (self.max_equity - current_equity) / self.max_equity
        else:
            drawdown = 0.0

        drawdown_penalty = drawdown * 100.0  # scale to percentage
        reward = pnl_return - 0.01 * drawdown_penalty

        return float(reward)

    def _is_done(self) -> bool:
        """Check if episode is done.

        Terminates when:
        - All bars processed
        - Capital falls below 10% of initial capital
        """
        if self.current_step >= self.n_bars - 1:
            return True

        if self.capital < self.initial_capital * 0.1:
            return True

        return False

    def _get_state(self) -> np.ndarray:
        """Build the state vector from the current bar.

        Returns:
            7-dim numpy array: [price, RSI, MACD, BB_position, volume_ratio, position, pnl_pct]
        """
        if self.current_step >= self.n_bars:
            return np.zeros(7, dtype=np.float32)

        # Normalize price by initial price
        price_norm = self.close[self.current_step] / self.initial_price if self.initial_price > 0 else 0.0

        # RSI (0-100)
        rsi = self.rsi_vals[self.current_step]
        if np.isnan(rsi):
            rsi = 50.0

        # MACD histogram
        macd = self.macd_vals[self.current_step]
        if np.isnan(macd):
            macd = 0.0

        # BB position (0 = lower band, 0.5 = middle, 1 = upper band)
        bb_pos = 0.5
        if not np.isnan(self.bb_width[self.current_step]) and self.bb_width[self.current_step] > 0:
            bb_mid = self.close[self.current_step]
            bb_lower = self.close[self.current_step] - self.bb_width[self.current_step] * self.close[self.current_step] / 2
            bb_upper = self.close[self.current_step] + self.bb_width[self.current_step] * self.close[self.current_step] / 2
            if bb_upper > bb_lower:
                bb_pos = (self.close[self.current_step] - bb_lower) / (bb_upper - bb_lower)
                bb_pos = np.clip(bb_pos, 0.0, 1.0)

        # Volume ratio
        vol_r = self.vol_ratio[self.current_step]
        if np.isnan(vol_r):
            vol_r = 1.0

        # PnL percentage
        if self.position != 0 and self.entry_price > 0:
            if self.position == 1:
                pnl_pct = (self.close[self.current_step] - self.entry_price) / self.entry_price * self.leverage
            else:
                pnl_pct = (self.entry_price - self.close[self.current_step]) / self.entry_price * self.leverage
        else:
            pnl_pct = 0.0

        state = np.array([
            price_norm,
            rsi / 100.0,  # normalize to 0-1
            np.tanh(macd),  # squash MACD to [-1, 1]
            bb_pos,
            np.clip(vol_r / 3.0, 0.0, 1.0),  # normalize volume ratio
            float(self.position),
            np.tanh(pnl_pct),  # squash PnL to [-1, 1]
        ], dtype=np.float32)

        return state

    def render(self):
        """Print current state summary."""
        state = self._get_state()
        print(f"Step: {self.current_step}/{self.n_bars}")
        print(f"  Capital: {self.capital:.2f} | Equity: {self._compute_equity():.2f}")
        print(f"  Position: {self.position} | Entry: {self.entry_price:.4f}")
        print(f"  Price: {self.close[self.current_step]:.4f}")
        print(f"  State: price={state[0]:.4f} rsi={state[1]*100:.1f} macd={state[2]:.4f} "
              f"bb={state[3]:.3f} vol={state[4]:.3f} pos={state[5]:.0f} pnl={state[6]:.4f}")
        print(f"  Equity curve length: {len(self.equity_curve)}")

    def get_equity_curve(self) -> list:
        """Return the equity curve for analysis."""
        return self.equity_curve
