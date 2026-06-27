"""
Multi-Agent Strategy — Uses TechnicalAgent + RiskAgent + DecisionAgent for trade decisions.

Leverages a collaborative multi-agent system where each agent specializes in
a different aspect of trading: technical analysis, risk management, and
decision fusion. A review agent provides post-trade feedback.
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional

from strategy.base import Strategy, Signal, SignalType


class MultiAgentStrategy(Strategy):
    """Strategy using a collaborative multi-agent system for trade decisions.

    Parameters:
        min_confidence: Minimum confidence threshold for trade execution (default 0.6)
        use_orderbook: Whether to incorporate order book data (default False)
        leverage: Trading leverage multiplier (default 3)
        tech_weight: Weight for technical agent in decision (default 0.5)
        risk_weight: Weight for risk agent in decision (default 0.3)
        sentiment_weight: Weight for sentiment in decision (default 0.2)
        atr_period: Period for ATR-based stop loss (default 14)
        atr_stop_mult: ATR multiplier for stop loss (default 2.0)
    """

    def _default_params(self) -> Dict:
        return {
            'min_confidence': 0.6,
            'use_orderbook': False,
            'leverage': 3,
            'tech_weight': 0.5,
            'risk_weight': 0.3,
            'sentiment_weight': 0.2,
            'atr_period': 14,
            'atr_stop_mult': 2.0,
            'risk_reward_ratio': 2.0,
        }

    @classmethod
    def get_param_info(cls):
        return [
            {'name': 'min_confidence', 'type': 'float', 'default': 0.6,
             'min': 0.0, 'max': 1.0, 'description': 'Minimum confidence to execute a trade'},
            {'name': 'use_orderbook', 'type': 'bool', 'default': False,
             'description': 'Use order book data for confirmation'},
            {'name': 'leverage', 'type': 'int', 'default': 3,
             'min': 1, 'max': 100, 'description': 'Trading leverage multiplier'},
            {'name': 'tech_weight', 'type': 'float', 'default': 0.5,
             'min': 0.0, 'max': 1.0, 'description': 'Weight of technical analysis'},
            {'name': 'risk_weight', 'type': 'float', 'default': 0.3,
             'min': 0.0, 'max': 1.0, 'description': 'Weight of risk assessment'},
            {'name': 'sentiment_weight', 'type': 'float', 'default': 0.2,
             'min': 0.0, 'max': 1.0, 'description': 'Weight of market sentiment'},
            {'name': 'atr_period', 'type': 'int', 'default': 14,
             'min': 5, 'max': 50, 'description': 'ATR period for stop loss calculation'},
            {'name': 'atr_stop_mult', 'type': 'float', 'default': 2.0,
             'min': 0.5, 'max': 5.0, 'description': 'ATR multiplier for stop loss'},
            {'name': 'risk_reward_ratio', 'type': 'float', 'default': 2.0,
             'min': 1.0, 'max': 5.0, 'description': 'Risk/reward ratio for take profit'},
        ]

    def init(self):
        """Initialize the multi-agent system and pre-compute indicators."""
        from ai.agents import TechnicalAgent, RiskAgent, DecisionAgent, ReviewAgent

        # Initialize agents
        self.tech_agent = TechnicalAgent()
        self.risk_agent = RiskAgent(
            atr_period=self.get_param('atr_period', 14),
            atr_stop_mult=self.get_param('atr_stop_mult', 2.0),
            risk_reward_ratio=self.get_param('risk_reward_ratio', 2.0),
        )
        self.decision_agent = DecisionAgent(
            tech_weight=self.get_param('tech_weight', 0.5),
            risk_weight=self.get_param('risk_weight', 0.3),
            sentiment_weight=self.get_param('sentiment_weight', 0.2),
            min_confidence=self.get_param('min_confidence', 0.6),
        )
        self.review_agent = ReviewAgent()

        # Optional order book analyzer
        self.orderbook = None
        if self.get_param('use_orderbook', False):
            from ai.orderbook import OrderBookAnalyzer
            self.orderbook = OrderBookAnalyzer(symbol='BTCUSDT', depth=20)

        # Pre-compute ATR for risk evaluation
        if self.data is not None and len(self.data) > 0:
            close = self.data['close'].values.astype(float)
            high = self.data['high'].values.astype(float) if 'high' in self.data.columns else close
            low = self.data['low'].values.astype(float) if 'low' in self.data.columns else close
            self._atr_values = self.atr(high, low, close, period=self.get_param('atr_period', 14))
            self.add_indicator('atr', self._atr_values)

        # Trade tracking for review agent
        self._trades_history: list = []
        self._entry_time = 0

    def next(self, i: int) -> Signal:
        """Process the current bar and return a trading signal.

        Args:
            i: Current bar index

        Returns:
            Signal object indicating the action to take.
        """
        if self.data is None:
            return Signal(SignalType.HOLD, '', 0.0, reason='No data')

        # Skip bars without enough history
        if i < 30:
            return Signal(SignalType.HOLD, '', 0.0, reason='Warming up')

        price = float(self.data['close'].iloc[i])

        # Step 1: Technical analysis
        tech_result = self.tech_agent.analyze(self.data, i)

        # Step 2: Risk evaluation
        account = self._build_account_state(i)
        risk_result = self.risk_agent.evaluate(
            account,
            tech_result['signal'],
            price,
        )

        # Step 3: Get sentiment score (from order book if enabled, otherwise neutral)
        sentiment_score = 0.5
        if self.orderbook is not None:
            try:
                if self.orderbook.fetch():
                    imbalance = self.orderbook.get_imbalance()
                    sentiment_score = (imbalance + 1.0) / 2.0  # map [-1,1] to [0,1]
            except Exception:
                pass

        # Step 4: Decision fusion
        decision = self.decision_agent.decide(tech_result, risk_result, sentiment_score)

        # Step 5: Execute based on decision
        current_position = self.get_position()

        if decision['action'] == 'BUY':
            if current_position == 0:
                reason = f"MultiAgent: {tech_result['reason']} | conf={decision['confidence']:.2f}"
                return Signal(
                    signal_type=SignalType.BUY,
                    symbol='',
                    price=price,
                    quantity=decision['size'],
                    stop_loss=decision['sl'],
                    take_profit=decision['tp'],
                    reason=reason,
                )
            elif current_position == -1:
                reason = f"MultiAgent: BUY signal, closing short | conf={decision['confidence']:.2f}"
                return Signal(
                    signal_type=SignalType.CLOSE_SHORT,
                    symbol='',
                    price=price,
                    reason=reason,
                )

        elif decision['action'] == 'SELL':
            if current_position == 0:
                reason = f"MultiAgent: {tech_result['reason']} | conf={decision['confidence']:.2f}"
                return Signal(
                    signal_type=SignalType.SELL,
                    symbol='',
                    price=price,
                    quantity=decision['size'],
                    stop_loss=decision['sl'],
                    take_profit=decision['tp'],
                    reason=reason,
                )
            elif current_position == 1:
                reason = f"MultiAgent: SELL signal, closing long | conf={decision['confidence']:.2f}"
                return Signal(
                    signal_type=SignalType.CLOSE_LONG,
                    symbol='',
                    price=price,
                    reason=reason,
                )

        # Track position entry for review
        if current_position == 0:
            self._entry_time = i
        elif current_position != 0 and self._entry_time > 0:
            # Position is open, check if we should track exit
            pass

        return Signal(SignalType.HOLD, '', price, reason='No action')

    def _build_account_state(self, i: int) -> Dict:
        """Build a mock account state dict for the RiskAgent.

        Args:
            i: Current bar index

        Returns:
            dict with capital, positions, daily_pnl, daily_loss, atr
        """
        atr_val = 0.01 * self.data['close'].iloc[i]  # default 1% of price
        if 'atr' in self._indicators:
            atr_arr = self._indicators['atr']
            if i < len(atr_arr) and not np.isnan(atr_arr[i]):
                atr_val = float(atr_arr[i])

        return {
            'capital': 10000.0,
            'positions': 1 if self.get_position() != 0 else 0,
            'daily_pnl': 0.0,
            'daily_loss': 0.0,
            'atr': atr_val,
        }

    def record_trade(self, entry_price: float, exit_price: float, signal_type: str,
                     pnl: float, entry_time: int, exit_time: int):
        """Record a completed trade for the review agent.

        Args:
            entry_price: Entry price
            exit_price: Exit price
            signal_type: Type of signal that triggered entry
            pnl: Profit/loss amount
            entry_time: Entry bar index
            exit_time: Exit bar index
        """
        self._trades_history.append({
            'signal_type': signal_type,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'pnl_pct': (exit_price - entry_price) / entry_price if entry_price > 0 else 0.0,
            'entry_time': entry_time,
            'exit_time': exit_time,
        })

    def get_review(self) -> Dict:
        """Get performance review from the ReviewAgent.

        Returns:
            dict with trade analysis and feedback
        """
        analysis = self.review_agent.review(self._trades_history)
        analysis['feedback'] = self.review_agent.get_feedback()
        return analysis
