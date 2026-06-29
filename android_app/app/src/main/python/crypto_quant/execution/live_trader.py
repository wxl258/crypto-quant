"""
Live Paper Trading Engine — Runs strategies against real-time exchange prices.

Features:
- Fetches prices from exchange public API (no auth needed)
- Supports Binance and OKX
- Runs the selected strategy on each tick
- Executes simulated trades with full risk management
- Persists all trades to database
- Broadcasts updates via WebSocket

Price sources in priority order:
1. Exchange public REST API (Binance /api/v3/ticker/price or OKX /api/v5/market/ticker)
2. Internal MarketDataCollector via ccxt
3. Simulated random-walk (configurable, disabled by default)
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import requests

from config import get_db_path, get_trading_config, get_exchange_id
from data.store import DataStore
from execution.notifier import get_notifier
from execution.simulator import PaperTradingSimulator
from strategy.base import SignalType, Strategy

logger = logging.getLogger(__name__)


class LivePaperTrader:
    """Runs a strategy in paper trading mode against live market data.

    The trader fetches prices on a configurable interval, feeds them to
    the strategy, and routes generated signals to a paper trading simulator.
    Risk management (stop-loss / take-profit) is checked on every tick.

    Attributes:
        strategy: The strategy instance being run.
        symbol: Trading pair symbol (e.g. ``'BTCUSDT'``).
        simulator: Paper trading simulator for order execution.
        store: Data store for persisting trades.
        interval: Tick interval in seconds.
    """

    def __init__(
        self,
        strategy: Strategy,
        symbol: str,
        simulator: PaperTradingSimulator,
        store: DataStore,
        interval_seconds: int = 60,
    ) -> None:
        """Initialise the live paper trader.

        Args:
            strategy: A configured strategy instance.
            symbol: Trading pair symbol.
            simulator: Paper trading simulator.
            store: Data store for persisting trades.
            interval_seconds: Tick interval in seconds (default 60).
        """
        self.strategy: Strategy = strategy
        self.symbol: str = symbol
        self.simulator: PaperTradingSimulator = simulator
        self.store: DataStore = store
        self.interval: int = interval_seconds

        self._running: bool = False
        self._task: asyncio.Task[None] | None = None
        self._last_price: float = 0.0
        self._last_tick: datetime | None = None
        self._tick_count: int = 0
        self._trade_count: int = 0
        self._start_time: datetime | None = None
        self._error_count: int = 0

        # Price history for strategy context (last N candles)
        self._price_history: list[dict[str, Any]] = []
        self._max_history: int = 100

        # Strategy init guard — only run init() once
        self._init_called: bool = False

        # Lazy-reused MarketDataCollector for fallback price fetch
        self._collector = None

        # Offline fallback config: offline_pause=true means stop trading when offline
        self._allow_fallback_simulation: bool = not get_trading_config().get(
            "offline_pause", True
        )

    def _fetch_price(self) -> dict[str, Any] | None:
        """Fetch current price from available sources in priority order.

        1. Exchange public REST API (Binance or OKX, based on config)
        2. Internal ccxt collector
        3. Simulated random-walk (only when ``_allow_fallback_simulation`` is
           ``True``)

        Returns:
            A ticker dict with at least ``symbol`` and ``last`` keys,
            or ``None`` if no source is available.
        """
        exchange_id = get_exchange_id()

        # Try 1: Exchange public API (no auth required for price)
        try:
            if exchange_id == 'okx':
                # OKX public ticker endpoint
                url = f"https://www.okx.com/api/v5/market/ticker?instId={self.symbol}-USDT-SWAP"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('code') == '0' and data.get('data'):
                        ticker_data = data['data'][0]
                        price = float(ticker_data['last'])
                        return {
                            'symbol': self.symbol,
                            'last': price,
                            'bid': float(ticker_data.get('bidPx', price)),
                            'ask': float(ticker_data.get('askPx', price)),
                            'high': float(ticker_data.get('high24h', price)),
                            'low': float(ticker_data.get('low24h', price)),
                            'volume': float(ticker_data.get('vol24h', 0)),
                        }
            else:
                # Binance public ticker endpoint
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={self.symbol}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        'symbol': data['symbol'],
                        'last': float(data['price']),
                        'bid': float(data['price']) * 0.999,
                        'ask': float(data['price']) * 1.001,
                    }
        except Exception as e:
            logger.debug(f"{exchange_id} public API price fetch failed for {self.symbol}: {e}")

        # Try 2: Use ccxt collector if available (lazy init, reused across ticks)
        try:
            from data.collector import MarketDataCollector

            if self._collector is None:
                store = DataStore(get_db_path())
                self._collector = MarketDataCollector(store, testnet=False)
            ticker = self._collector.get_ticker(self.symbol)
            if ticker and ticker.get('last'):
                return ticker
        except Exception as e:
            logger.debug(f"CCXT collector price fetch failed for {self.symbol}: {e}")
            pass

        # Try 3: Simulated price (random walk from last known price)
        # Only allowed when explicitly configured, to prevent trading on fake data
        if not self._allow_fallback_simulation:
            logger.warning(
                f"No real price data available for {self.symbol}, "
                f"fallback simulation disabled (offline_pause=true)"
            )
            return None

        base = self._last_price if self._last_price > 0 else 50000
        sim_price = base * (1 + random.gauss(0, 0.002))  # 0.2% std dev
        return {
            'symbol': self.symbol,
            'last': sim_price,
            'bid': sim_price * 0.999,
            'ask': sim_price * 1.001,
            '_simulated': True,
        }

    @property
    def is_running(self) -> bool:
        """Whether the trading loop is currently active."""
        return self._running

    @property
    def status(self) -> dict[str, Any]:
        """Snapshot of the trader's current state.

        Returns:
            A dict with keys: ``running``, ``symbol``, ``last_price``,
            ``last_tick``, ``tick_count``, ``trade_count``,
            ``start_time``, ``error_count``, ``uptime_seconds``.
        """
        return {
            'running': self._running,
            'symbol': self.symbol,
            'last_price': self._last_price,
            'last_tick': str(self._last_tick) if self._last_tick else None,
            'tick_count': self._tick_count,
            'trade_count': self._trade_count,
            'start_time': str(self._start_time) if self._start_time else None,
            'error_count': self._error_count,
            'uptime_seconds': (
                (datetime.now() - self._start_time).total_seconds()
                if self._start_time
                else 0
            ),
        }

    async def start(self) -> None:
        """Start the live trading loop as a background asyncio task.

        No-op if the trader is already running.
        """
        if self._running:
            return
        self._running = True
        self._start_time = datetime.now()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Live paper trader started for {self.symbol}")

    async def stop(self) -> None:
        """Stop the live trading loop and cancel the background task.

        No-op if the trader is not running.
        """
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Expected during task cancellation, no action needed
        logger.info(f"Live paper trader stopped for {self.symbol}")

    async def _run_loop(self) -> None:
        """Main trading loop — runs one tick every ``interval`` seconds."""
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                logger.error(f"Tick error for {self.symbol}: {e}")

            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        """Process a single trading tick.

        1. Fetches current price via ``_fetch_price``.
        2. Updates price history and simulator.
        3. Checks stop-loss / take-profit conditions for open positions.
        4. Runs the strategy and executes signals if enough history exists.
        """
        # Fetch current price via public API
        ticker = await asyncio.to_thread(self._fetch_price)
        if not ticker or not ticker.get('last'):
            logger.warning(f"No price data for {self.symbol}")
            return

        price: float = ticker['last']
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

        # Update simulator with current price (includes stop-check internally)
        self.simulator.update_price(self.symbol, price, self._last_tick)

        # If we have enough history, run strategy
        if len(self._price_history) >= 30:
            df = pd.DataFrame(self._price_history)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)

            # Initialise strategy once when data is first available
            if not self._init_called:
                self.strategy.set_data(df)
                self.strategy.init()
                self._init_called = True
            else:
                # On subsequent ticks, only update data reference
                self.strategy.set_data(df)

            # Get signal from strategy for the latest bar
            signal = self.strategy.next(len(df) - 1)

            if signal.signal_type in (SignalType.BUY, SignalType.SELL):
                side = 'LONG' if signal.signal_type == SignalType.BUY else 'SHORT'
                signal_name = 'BUY' if signal.signal_type == SignalType.BUY else 'SELL'

                # Push signal notification
                strategy_name = self.strategy.__class__.__name__
                confidence: float = getattr(signal, 'confidence', 0.0)
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
                    # Push trade notification
                    quantity = (
                        order.get('quantity', 0)
                        if isinstance(order, dict)
                        else 0
                    )
                    get_notifier().trade_alert(self.symbol, side, price, quantity)

            elif signal.signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                side = (
                    'LONG' if signal.signal_type == SignalType.CLOSE_LONG else 'SHORT'
                )
                self.simulator.close_position(
                    self.symbol, price,
                    f"策略信号: {signal.reason}",
                    self._last_tick,
                )
                self._trade_count += 1
                logger.info(
                    f"Auto-closed {self.symbol} @ {price} ({signal.reason})"
                )
                # Push close notification
                get_notifier().trade_alert(self.symbol, side, price, 0)
