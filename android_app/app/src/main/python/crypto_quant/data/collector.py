"""
Market Data Collector - Fetches OHLCV data from Binance
"""
import asyncio
import ccxt
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, List
import logging

from .store import DataStore

logger = logging.getLogger(__name__)


class MarketDataCollector:
    """Collects market data from supported exchanges (Binance, OKX, Bybit)"""
    
    INTERVAL_MAP = {
        '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1h': '1h', '4h': '4h', '1d': '1d'
    }
    
    EXCHANGE_MAP = {
        'binance': ccxt.binance,
        'okx': ccxt.okx,
        'bybit': ccxt.bybit,
    }
    
    def __init__(self, store: DataStore, testnet: bool = True, exchange_id: str = "binance"):
        self.store = store
        self.exchange_id = exchange_id.lower()
        
        exchange_cls = self.EXCHANGE_MAP.get(self.exchange_id)
        if exchange_cls is None:
            raise ValueError(f"Unsupported exchange: {self.exchange_id}. "
                           f"Supported: {list(self.EXCHANGE_MAP.keys())}")
        
        exchange_opts = {
            'enableRateLimit': True,
        }
        # Set default type to future for Binance
        if self.exchange_id == 'binance':
            exchange_opts['options'] = {'defaultType': 'future'}
        
        self.exchange = exchange_cls(exchange_opts)
        
        if testnet:
            self.exchange.set_sandbox_mode(True)
    
    def fetch_ohlcv(self, symbol: str, interval: str, 
                    since: Optional[int] = None,
                    limit: int = 1000) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from exchange"""
        try:
            ccxt_interval = self.INTERVAL_MAP.get(interval, interval)
            
            ohlcv = self.exchange.fetch_ohlcv(
                symbol, ccxt_interval, since=since, limit=limit
            )
            
            if not ohlcv:
                return None
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
            
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {symbol} {interval}: {e}")
            return None
    
    def fetch_and_store(self, symbol: str, interval: str, 
                        limit: int = 500) -> Optional[pd.DataFrame]:
        """Fetch data and store to database"""
        df = self.fetch_ohlcv(symbol, interval, limit=limit)
        if df is not None and not df.empty:
            self.store.save_ohlcv(symbol, interval, df)
            logger.info(f"Stored {len(df)} candles for {symbol} {interval}")
        return df
    
    def fetch_history(self, symbol: str, interval: str,
                      days: int = 30) -> Optional[pd.DataFrame]:
        """Fetch historical data for specified days"""
        all_dfs = []
        since = int((datetime.now().timestamp() - days * 86400) * 1000)
        max_iterations = 50  # Safety cap: prevent infinite loop
        
        for _ in range(max_iterations):
            df = self.fetch_ohlcv(symbol, interval, since=since, limit=1000)
            if df is None or df.empty:
                break
            
            all_dfs.append(df)
            # Advance since to one unit past the last candle
            new_since = int(df.index[-1].timestamp() * 1000) + 1
            if new_since <= since:
                # No progress — exchange returned same or earlier data
                logger.warning(f"No progress fetching {symbol} {interval}, breaking")
                break
            since = new_since
            
            if len(df) < 1000:
                break
        
        if not all_dfs:
            return None
        
        result = pd.concat(all_dfs)
        result = result[~result.index.duplicated(keep='first')]
        result.sort_index(inplace=True)
        
        # Store
        self.store.save_ohlcv(symbol, interval, result)
        return result
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Get current ticker info"""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            return None
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol"""
        ticker = self.get_ticker(symbol)
        return ticker['last'] if ticker else None
    
    async def fetch_multiple_symbols(self, symbols: List[str], interval: str,
                                     limit: int = 500):
        """Fetch data for multiple symbols concurrently"""
        tasks = []
        for symbol in symbols:
            tasks.append(asyncio.to_thread(
                self.fetch_and_store, symbol, interval, limit
            ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
