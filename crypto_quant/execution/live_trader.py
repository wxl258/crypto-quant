"""
Live Paper Trading Engine — Runs strategies against real-time Binance prices.

Features:
- Fetches prices from Binance public API (no auth needed)
- Runs the selected strategy on each tick
- Executes simulated trades with full risk management
- Persists all trades to database
- Broadcasts updates via WebSocket
"""
import asyncio
import logging
import time
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, List

from data.store import DataStore
from execution.simulator import PaperTradingSimulator
from execution.notifier import get_notifier
from risk.manager import RiskManager, RiskLimits
from strategy.base import Strategy, SignalType
from config import get_db_path, get_risk_config, get_trading_config

logger = logging.getLogger(__name__)


class LivePaperTrader:
    """Runs a strategy in paper trading mode against live market data."""

    def __init__(self, strategy: Strategy, symbol: str, simulator: PaperTradingSimulator,
                 store: DataStore, interval_seconds: int = 60):
        self.strategy = strategy
        self.symbol = symbol
        self.simulator = simulator
        self.store = store
        self.interval = interval_seconds

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_price = 0.0
        self._last_tick = None
        self._tick_count = 0
        self._trade_count = 0
        self._start_time = None
        self._error_count = 0

        # Price history for strategy context (last N candles)
        self._price_history: List[dict] = []
        self._max_history = 100

        # Offline fallback config: offline_pause=true means stop trading when offline
        self._allow_fallback_simulation = not get_trading_config().get(
            "offline_pause", True
        )

    def _fetch_price(self) -> Optional[dict]:
        """Fetch current price — tries Binance API, falls back to ccxt, then simulation."""
        # Try 1: Binance public API
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={self.symbol}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return {'symbol': data['symbol'], 'last': float(data['price']),
                        'bid': float(data['price'])*0.999, 'ask': float(data['price'])*1.001}
        except Exception:
            pass

        # Try 2: Use ccxt collector if available
        try:
            from data.collector import MarketDataCollector
            from data.store import DataStore
            store = DataStore(get_db_path())
            collector = MarketDataCollector(store, testnet=False)
            ticker = collector.get_ticker(self.symbol)
            if ticker and ticker.get('last'):
                return ticker
        except Exception:
            pass

        # Try 3: Simulated price (random walk from last known price)
        # Only allowed when explicitly configured, to prevent trading on fake data
        if not self._allow_fallback_simulation:
            logger.warning(
                f"No real price data available for {self.symbol}, "
                f"fallback simulation disabled (offline_pause=true)"
            )
            return None

        import random
        base = self._last_price if self._last_price > 0 else 50000
        sim_price = base * (1 + random.gauss(0, 0.002))  # 0.2% std dev
        return {'symbol': self.symbol, 'last': sim_price,
                'bid': sim_price*0.999, 'ask': sim_price*1.001,
                '_simulated': True}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def status(self) -> dict:
        return {
            'running': self._running,
            'symbol': self.symbol,
            'last_price': self._last_price,
            'last_tick': str(self._last_tick) if self._last_tick else None,
            'tick_count': self._tick_count,
            'trade_count': self._trade_count,
            'start_time': str(self._start_time) if self._start_time else None,
            'error_count': self._error_count,
            'uptime_seconds': (datetime.now() - self._start_time).total_seconds() if self._start_time else 0,
        }

    async def start(self):
        """Start the live trading loop."""
        if self._running:
            return
        self._running = True
        self._start_time = datetime.now()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Live paper trader started for {self.symbol}")

    async def stop(self):
        """Stop the live trading loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"Live paper trader stopped for {self.symbol}")

    async def _run_loop(self):
        """Main trading loop — runs every interval_seconds."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error(f"Tick error for {self.symbol}: {e}")

            await asyncio.sleep(self.interval)

    async def _tick(self):
        """Process one trading tick."""
        # Fetch current price via public API
        ticker = await asyncio.to_thread(self._fetch_price)
        if not ticker or not ticker.get('last'):
            logger.warning(f"No price data for {self.symbol}")
            return

        price = ticker['last']
        self._last_price = price
        self._last_tick = datetime.now()
        self._tick_count += 1

        # Update price history
        self._price_history.append({
            'timestamp': self._last_tick,
            'open': ticker.get('open', price),
            'high': ticker.get('high', price),
            'low': ticker.get('low', price),
            'close': price,
            'volume': ticker.get('volume', 0),
        })
        if len(self._price_history) > self._max_history:
            self._price_history = self._price_history[-self._max_history:]

        # Update simulator with current price
        self.simulator.update_price(self.symbol, price, self._last_tick)

        # Check stop conditions for open positions
        for sym in list(self.simulator.positions.keys()):
            if self.simulator.risk_manager.check_stop_conditions(sym, price):
                self.simulator.close_position(sym, price, "止损/止盈触发", self._last_tick)
                self._trade_count += 1
                logger.info(f"Auto-closed {sym} @ {price} (stop condition)")
                # 推送风控告警
                pos = self.simulator.positions.get(sym)
                side = pos.get('side', 'LONG') if pos else 'LONG'
                get_notifier().risk_alert(
                    f"{sym} 止损/止盈触发",
                    f"价格：${price:,.2f}\n原因：止损/止盈条件触发"
                )

        # If we have enough history, run strategy
        if len(self._price_history) >= 30:
            import pandas as pd
            import numpy as np

            df = pd.DataFrame(self._price_history)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)

            self.strategy.set_data(df)
            self.strategy.init()

            # Get signal from strategy for the latest bar
            signal = self.strategy.next(len(df) - 1)

            if signal.signal_type in (SignalType.BUY, SignalType.SELL):
                side = 'LONG' if signal.signal_type == SignalType.BUY else 'SHORT'
                signal_name = 'BUY' if signal.signal_type == SignalType.BUY else 'SELL'
                
                # 推送信号通知
                strategy_name = self.strategy.__class__.__name__
                confidence = getattr(signal, 'confidence', 0.0)
                get_notifier().signal_alert(
                    self.symbol, strategy_name, signal_name, price, confidence
                )
                
                order = self.simulator.open_position(
                    self.symbol, side, price,
                    leverage=self.strategy.get_param('leverage', 3),
                    timestamp=self._last_tick,
                )
                if order:
                    self._trade_count += 1
                    logger.info(f"Auto-opened {side} {self.symbol} @ {price}")
                    # 推送成交通知
                    quantity = order.get('quantity', 0) if isinstance(order, dict) else 0
                    get_notifier().trade_alert(self.symbol, side, price, quantity)

            elif signal.signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                side = 'LONG' if signal.signal_type == SignalType.CLOSE_LONG else 'SHORT'
                self.simulator.close_position(
                    self.symbol, price, f"策略信号: {signal.reason}", self._last_tick
                )
                self._trade_count += 1
                logger.info(f"Auto-closed {self.symbol} @ {price} ({signal.reason})")
                # 推送平仓通知
                get_notifier().trade_alert(self.symbol, side, price, 0)
