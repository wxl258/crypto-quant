"""
Multi-Exchange Futures Client — Supports Binance and OKX perpetual futures trading.
Uses ccxt unified API for both exchanges.
"""
import ccxt
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = ['binance', 'okx']


class MultiExchangeClient:
    """Unified futures trading client for Binance and OKX."""

    def __init__(self, exchange_id: str = "binance", api_key: str = "",
                 api_secret: str = "", password: str = "", testnet: bool = True):
        """
        Args:
            exchange_id: 'binance' or 'okx'
            api_key: API key
            api_secret: API secret
            password: OKX requires a passphrase (API密码), not used for Binance
            testnet: Use testnet/sandbox mode
        """
        if exchange_id not in SUPPORTED_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {exchange_id}. "
                             f"Supported: {SUPPORTED_EXCHANGES}")

        self.exchange_id = exchange_id
        self.testnet = testnet

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

    # ── Connection ──

    def is_connected(self) -> bool:
        try:
            self.exchange.fetch_time()
            return True
        except Exception:
            return False

    # ── Balance ──

    def get_balance(self, currency: str = "USDT") -> Dict:
        try:
            balance = self.exchange.fetch_balance()
            free = balance.get(currency, {}).get('free', 0) if isinstance(
                balance.get(currency), dict) else balance.get('free', {}).get(currency, 0)
            total = balance.get('total', {}).get(currency, 0) if isinstance(
                balance.get('total'), dict) else balance.get(currency, 0)
            return {'currency': currency, 'free': free, 'total': total}
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_balance failed: {e}")
            return {'currency': currency, 'free': 0, 'total': 0}

    # ── Positions ──

    def get_positions(self, symbol: str = None) -> List[Dict]:
        try:
            positions = self.exchange.fetch_positions(
                symbols=[symbol] if symbol else None)
            result = []
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

    # ── Leverage ──

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        try:
            self.exchange.set_leverage(leverage, symbol)
            return True
        except Exception as e:
            logger.error(f"[{self.exchange_id}] set_leverage failed: {e}")
            return False

    # ── Orders ──

    def create_market_order(self, symbol: str, side: str,
                            quantity: float) -> Optional[Dict]:
        try:
            order = self.exchange.create_order(
                symbol, 'market', side.lower(), quantity)
            return {
                'id': order.get('id', ''),
                'price': order.get('price', 0) or order.get('average', 0),
                'filled': order.get('filled', 0),
                'cost': order.get('cost', 0),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_market_order failed: {e}")
            return None

    def create_limit_order(self, symbol: str, side: str,
                           quantity: float, price: float) -> Optional[Dict]:
        try:
            order = self.exchange.create_order(
                symbol, 'limit', side.lower(), quantity, price)
            return {
                'id': order.get('id', ''),
                'price': price,
                'filled': order.get('filled', 0),
                'status': order.get('status', ''),
            }
        except Exception as e:
            logger.error(f"[{self.exchange_id}] create_limit_order failed: {e}")
            return None

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.error(f"[{self.exchange_id}] cancel_order failed: {e}")
            return False

    # ── Ticker / Price ──

    def get_ticker(self, symbol: str) -> Dict:
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
            return {'symbol': symbol, 'last': 0, 'bid': 0, 'ask': 0,
                    'high': 0, 'low': 0, 'volume': 0, 'change_pct': 0}

    def get_current_price(self, symbol: str) -> float:
        ticker = self.get_ticker(symbol)
        return ticker.get('last', 0)

    # ── Funding Rate ──

    def get_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            rate = self.exchange.fetch_funding_rate(symbol)
            return rate.get('fundingRate', 0) if rate else 0
        except Exception as e:
            logger.error(f"[{self.exchange_id}] get_funding_rate failed: {e}")
            return None


# ── Factory ──

def create_client(exchange_id: str, api_key: str = "", api_secret: str = "",
                  password: str = "", testnet: bool = True) -> MultiExchangeClient:
    """Factory to create exchange client by name."""
    return MultiExchangeClient(
        exchange_id=exchange_id.lower(),
        api_key=api_key,
        api_secret=api_secret,
        password=password,
        testnet=testnet,
    )


# Backward compatibility
BinanceFuturesClient = MultiExchangeClient
