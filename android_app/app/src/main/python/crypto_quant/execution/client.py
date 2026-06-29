"""
Multi-Exchange Futures Client — Supports Binance and OKX perpetual futures trading.

Uses ccxt unified API for both exchanges. Provides a unified interface for:
- Connection health checks
- Balance queries
- Position management
- Leverage configuration
- Order creation and cancellation (market/limit)
- Ticker and price data
- Funding rate lookups

Supported exchanges: 'binance', 'okx'.
"""

from __future__ import annotations

import logging
from typing import Any

import ccxt

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES: list[str] = ['binance', 'okx']


class MultiExchangeClient:
    """Unified futures trading client for Binance and OKX perpetual futures.

    Wraps ccxt exchange instances with a consistent API surface across
    supported exchanges. Handles sandbox/testnet mode for both Binance
    (future testnet) and OKX (swap demo trading).
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: str = "",
        api_secret: str = "",
        password: str = "",
        testnet: bool = True,
    ) -> None:
        """Initialise the multi-exchange client.

        Args:
            exchange_id: Exchange identifier — ``'binance'`` or ``'okx'``.
            api_key: Exchange API key.
            api_secret: Exchange API secret.
            password: OKX requires a passphrase (API password); ignored for Binance.
            testnet: When ``True``, enable sandbox/testnet mode.

        Raises:
            ValueError: If *exchange_id* is not one of the supported exchanges.
        """
        if exchange_id not in SUPPORTED_EXCHANGES:
            raise ValueError(
                f"Unsupported exchange: {exchange_id}. "
                f"Supported: {SUPPORTED_EXCHANGES}"
            )

        self.exchange_id: str = exchange_id
        self.testnet: bool = testnet

        if exchange_id == 'binance':
            self.exchange = ccxt.binance({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {'defaultType': 'future'},
            })
            if testnet:
                self.exchange.set_sandbox_mode(True)

        elif exchange_id == 'okx':
            self.exchange = ccxt.okx({
                'apiKey': api_key,
                'secret': api_secret,
                'password': password,
                'enableRateLimit': True,
                'options': {'defaultType': 'swap'},
            })
            if testnet:
                self.exchange.set_sandbox_mode(True)

    # ── Connection ──────────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        """Check exchange connectivity via a lightweight ``fetch_time`` call.

        Returns:
            ``True`` if the exchange responded successfully, ``False`` otherwise.
        """
        try:
            self.exchange.fetch_time()
            return True
        except Exception:
            return False

    # ── Balance ─────────────────────────────────────────────────────────────

    def get_balance(self, currency: str = "USDT") -> dict[str, Any]:
        """Retrieve free and total balance for a given currency.

        Args:
            currency: Asset code (e.g. ``'USDT'``, ``'BTC'``).

        Returns:
            A dict with keys ``'currency'``, ``'free'``, ``'total'``.
            Returns zero balances on error.
        """
        try:
            balance = self.exchange.fetch_balance()
            free = (
                balance.get(currency, {}).get('free', 0)
                if isinstance(balance.get(currency), dict)
                else balance.get('free', {}).get(currency, 0)
            )
            total = (
                balance.get('total', {}).get(currency, 0)
                if isinstance(balance.get('total'), dict)
                else balance.get(currency, 0)
            )
            return {'currency': currency, 'free': free, 'total': total}
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_balance failed: {e}")
            return {'currency': currency, 'free': 0, 'total': 0}

    # ── Positions ───────────────────────────────────────────────────────────

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Fetch open positions, optionally filtered by symbol.

        Args:
            symbol: Trading pair symbol (e.g. ``'BTC/USDT:USDT'``).
                If ``None``, return all open positions.

        Returns:
            A list of position dicts with keys: ``symbol``, ``side``,
            ``size``, ``entry_price``, ``unrealized_pnl``, ``leverage``.
        """
        try:
            positions = self.exchange.fetch_positions(
                symbols=[symbol] if symbol else None
            )
            result: list[dict[str, Any]] = []
            for p in positions:
                contracts = p.get('contracts', 0)
                if isinstance(contracts, (int, float)) and contracts > 0:
                    result.append({
                        'symbol': p.get('symbol', ''),
                        'side': p.get('side', ''),
                        'size': contracts,
                        'entry_price': p.get('entryPrice', 0),
                        'unrealized_pnl': p.get('unrealizedPnl', 0),
                        'leverage': p.get('leverage', 1),
                    })
            return result
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_positions failed: {e}")
            return []

    # ── Leverage ────────────────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a given symbol.

        Args:
            symbol: Trading pair symbol.
            leverage: Leverage multiplier (integer).

        Returns:
            ``True`` on success, ``False`` on error.
        """
        try:
            self.exchange.set_leverage(leverage, symbol)
            return True
        except Exception as e:
            logger.error(f"[{self.exchange_id}] set_leverage failed: {e}")
            return False

    # ── Orders ──────────────────────────────────────────────────────────────

    def create_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> dict[str, Any] | None:
        """Create a market order.

        Args:
            symbol: Trading pair symbol.
            side: ``'buy'`` or ``'sell'``.
            quantity: Order quantity in base currency.

        Returns:
            Order result dict with keys ``id``, ``price``, ``filled``,
            ``cost``, or ``None`` on error.
        """
        try:
            order = self.exchange.create_order(
                symbol, 'market', side.lower(), quantity
            )
            return {
                'id': order.get('id', ''),
                'price': order.get('price', 0) or order.get('average', 0),
                'filled': order.get('filled', 0),
                'cost': order.get('cost', 0),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_market_order failed: {e}")
            return None

    def create_limit_order(
        self, symbol: str, side: str, quantity: float, price: float
    ) -> dict[str, Any] | None:
        """Create a limit order.

        Args:
            symbol: Trading pair symbol.
            side: ``'buy'`` or ``'sell'``.
            quantity: Order quantity in base currency.
            price: Limit price.

        Returns:
            Order result dict with keys ``id``, ``price``, ``filled``,
            ``status``, or ``None`` on error.
        """
        try:
            order = self.exchange.create_order(
                symbol, 'limit', side.lower(), quantity, price
            )
            return {
                'id': order.get('id', ''),
                'price': price,
                'filled': order.get('filled', 0),
                'status': order.get('status', ''),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_limit_order failed: {e}")
            return None

    def create_stop_order(
        self, symbol: str, side: str, quantity: float, stop_price: float
    ) -> dict[str, Any] | None:
        """Create a stop-market order on the exchange (exchange-native stop-loss).

        The *side* should be opposite of the position side:
        - LONG position → ``side='sell'`` (stop triggers when price drops).
        - SHORT position → ``side='buy'`` (stop triggers when price rises).

        Args:
            symbol: Trading pair symbol.
            side: ``'buy'`` or ``'sell'`` — direction of the stop order.
            quantity: Order quantity in base currency.
            stop_price: Trigger price for the stop order.

        Returns:
            Order result dict with ``id`` and ``status``, or ``None`` on error.
        """
        try:
            order = self.exchange.create_order(
                symbol, 'stop_market', side.lower(), quantity, None,
                {'stopPrice': stop_price}
            )
            return {
                'id': order.get('id', ''),
                'status': order.get('status', ''),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_stop_order failed: {e}")
            return None

    def create_take_profit_order(
        self, symbol: str, side: str, quantity: float, tp_price: float
    ) -> dict[str, Any] | None:
        """Create a take-profit market order on the exchange (exchange-native take-profit).

        The *side* should be opposite of the position side:
        - LONG position → ``side='sell'`` (profit-taking).
        - SHORT position → ``side='buy'`` (profit-taking).

        Args:
            symbol: Trading pair symbol.
            side: ``'buy'`` or ``'sell'`` — direction of the TP order.
            quantity: Order quantity in base currency.
            tp_price: Trigger price for the take-profit order.

        Returns:
            Order result dict with ``id`` and ``status``, or ``None`` on error.
        """
        try:
            order = self.exchange.create_order(
                symbol, 'take_profit_market', side.lower(), quantity, None,
                {'stopPrice': tp_price}
            )
            return {
                'id': order.get('id', ''),
                'status': order.get('status', ''),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_take_profit_order failed: {e}")
            return None

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an existing order.

        Args:
            order_id: Exchange order ID.
            symbol: Trading pair symbol the order belongs to.

        Returns:
            ``True`` on success, ``False`` on error.
        """
        try:
            self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"[{self.exchange_id}] cancel_order failed: {e}")
            return False

    # ── Ticker / Price ──────────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch the latest ticker data for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            A dict with keys: ``symbol``, ``last``, ``bid``, ``ask``,
            ``high``, ``low``, ``volume``, ``change_pct``.
            Returns zero values on error.
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                'symbol': symbol,
                'last': ticker.get('last', 0),
                'bid': ticker.get('bid', 0),
                'ask': ticker.get('ask', 0),
                'high': ticker.get('high', 0),
                'low': ticker.get('low', 0),
                'volume': ticker.get('baseVolume', 0),
                'change_pct': ticker.get('percentage', 0),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_ticker failed: {e}")
            return {
                'symbol': symbol, 'last': 0, 'bid': 0, 'ask': 0,
                'high': 0, 'low': 0, 'volume': 0, 'change_pct': 0,
            }

    def get_current_price(self, symbol: str) -> float:
        """Get the last traded price for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Last price as a float; ``0`` if unavailable.
        """
        ticker = self.get_ticker(symbol)
        return ticker.get('last', 0)

    # ── Funding Rate ────────────────────────────────────────────────────────

    def get_funding_rate(self, symbol: str) -> float | None:
        """Fetch the current funding rate for a perpetual symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Funding rate as a float, or ``None`` on error.
        """
        try:
            rate = self.exchange.fetch_funding_rate(symbol)
            return rate.get('fundingRate', 0) if rate else 0
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_funding_rate failed: {e}")
            return None


# ── Factory ─────────────────────────────────────────────────────────────────


def create_client(
    exchange_id: str,
    api_key: str = "",
    api_secret: str = "",
    password: str = "",
    testnet: bool = True,
) -> MultiExchangeClient:
    """Factory to create an exchange client by name.

    Args:
        exchange_id: ``'binance'`` or ``'okx'`` (case-insensitive).
        api_key: Exchange API key.
        api_secret: Exchange API secret.
        password: OKX passphrase (ignored for Binance).
        testnet: Whether to use sandbox/testnet mode.

    Returns:
        A configured :class:`MultiExchangeClient` instance.
    """
    return MultiExchangeClient(
        exchange_id=exchange_id.lower(),
        api_key=api_key,
        api_secret=api_secret,
        password=password,
        testnet=testnet,
    )


# Backward compatibility alias
BinanceFuturesClient = MultiExchangeClient
