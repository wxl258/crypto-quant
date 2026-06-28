"""
Web API Routes — FastAPI endpoints for the trading system.
Resilient against Chaquopy Android environment where ccxt/pandas may be unavailable.
"""
import logging
import json
import os
import io
import itertools
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, List, Literal

# ── Safe framework imports (these are always available) ──
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# ── Pydantic v1/v2 compatibility ──
try:
    from pydantic import field_validator
except ImportError:
    try:
        from pydantic import validator as field_validator
    except ImportError:
        field_validator = None  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Dedicated thread pool for backtest comparison
_backtest_compare_executor = ThreadPoolExecutor(max_workers=4)

# ── Module availability flags (lazy-checked) ──
_MODULE_STATUS = {}

def _module_available(name: str, import_path: str) -> bool:
    """Check if a module can be imported. Caches result."""
    if name not in _MODULE_STATUS:
        try:
            __import__(import_path)
            _MODULE_STATUS[name] = True
        except ImportError:
            _MODULE_STATUS[name] = False
            logger.warning(f"Module '{import_path}' is not available in this environment")
    return _MODULE_STATUS[name]


# ── Safe lazy imports ──

def _import_config():
    try:
        from config import (
            get_db_path, get_trading_symbols, get_trading_config,
            get_risk_config, get_backtest_config, get_mode, get_binance_config,
            get_alerts_config, get_exchange_config, get_config,
            get_exchange_id, get_okx_config,
        )
        return {
            'get_db_path': get_db_path,
            'get_trading_symbols': get_trading_symbols,
            'get_trading_config': get_trading_config,
            'get_risk_config': get_risk_config,
            'get_backtest_config': get_backtest_config,
            'get_mode': get_mode,
            'get_binance_config': get_binance_config,
            'get_alerts_config': get_alerts_config,
            'get_exchange_config': get_exchange_config,
            'get_config': get_config,
            'get_exchange_id': get_exchange_id,
            'get_okx_config': get_okx_config,
        }
    except ImportError as e:
        logger.error(f"Failed to import config module: {e}")
        raise


def _import_store():
    try:
        from data.store import DataStore
        return DataStore
    except ImportError as e:
        logger.error(f"Failed to import DataStore: {e}")
        raise


def _import_collector():
    try:
        from data.collector import MarketDataCollector
        return MarketDataCollector
    except ImportError as e:
        logger.error(f"Failed to import MarketDataCollector: {e}")
        raise


def _import_pandas_numpy():
    """Import pandas and numpy, raising a unified error if unavailable."""
    try:
        import pandas as pd
        import numpy as np
        return pd, np
    except ImportError as e:
        raise ImportError(f"pandas/numpy not available in this environment: {e}")


def _import_fpdf():
    try:
        from fpdf import FPDF
        return FPDF
    except ImportError as e:
        raise ImportError(f"fpdf not available: {e}")


def _import_strategy_registry():
    try:
        from strategy import StrategyRegistry
        return StrategyRegistry
    except ImportError as e:
        raise ImportError(f"StrategyRegistry not available: {e}")


def _import_backtest_engine():
    try:
        from backtest.engine import BacktestEngine
        return BacktestEngine
    except ImportError as e:
        raise ImportError(f"BacktestEngine not available: {e}")


def _import_simulator():
    try:
        from execution.simulator import PaperTradingSimulator
        return PaperTradingSimulator
    except ImportError as e:
        raise ImportError(f"PaperTradingSimulator not available: {e}")


def _import_scheduler():
    try:
        from execution.scheduler import scheduler as _sched
        return _sched
    except ImportError:
        return None


def _import_risk_limits():
    try:
        from risk.manager import RiskLimits
        return RiskLimits
    except ImportError as e:
        raise ImportError(f"RiskLimits not available: {e}")


def _import_alert_manager():
    try:
        from web.alerts import AlertManager
        return AlertManager
    except ImportError as e:
        raise ImportError(f"AlertManager not available: {e}")


def _import_ws_manager():
    try:
        from web.websocket import ws_manager
        return ws_manager
    except ImportError:
        return None


# ── Pydantic request models ──

class BacktestRequest(BaseModel):
    strategy: str = "dual_ma"
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    initial_capital: float = Field(default=10000, gt=0)
    params: dict = {}
    days: int = Field(default=90, ge=1, le=3650)
    date_start: Optional[str] = None
    date_end: Optional[str] = None

    @field_validator('symbol')
    @classmethod
    def validate_symbol_backtest(cls, v):
        try:
            cfg = _import_config()
            allowed = cfg['get_trading_symbols']()
        except Exception:
            return v
        if v not in allowed:
            raise ValueError(f"symbol must be one of {allowed}")
        return v


class TradeRequest(BaseModel):
    symbol: str = "BTCUSDT"
    side: Literal["LONG", "SHORT"]
    leverage: int = Field(default=3, ge=1, le=125)

    @field_validator('symbol')
    @classmethod
    def validate_symbol_trade(cls, v):
        try:
            cfg = _import_config()
            allowed = cfg['get_trading_symbols']()
        except Exception:
            return v
        if v not in allowed:
            raise ValueError(f"symbol must be one of {allowed}")
        return v


class CloseTradeRequest(BaseModel):
    symbol: str = "BTCUSDT"

    @field_validator('symbol')
    @classmethod
    def validate_symbol_close(cls, v):
        try:
            cfg = _import_config()
            allowed = cfg['get_trading_symbols']()
        except Exception:
            return v
        if v not in allowed:
            raise ValueError(f"symbol must be one of {allowed}")
        return v


class RiskLimitsRequest(BaseModel):
    max_position_pct: Optional[float] = Field(default=None, ge=0.01, le=1.0)
    max_daily_loss_pct: Optional[float] = Field(default=None, ge=0.001, le=1.0)
    stop_loss_pct: Optional[float] = Field(default=None, ge=0.001, le=0.5)
    take_profit_pct: Optional[float] = Field(default=None, ge=0.001, le=2.0)


class ModeRequest(BaseModel):
    mode: Literal["paper", "live"]


class AlertConfigRequest(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False


class AlertTestRequest(BaseModel):
    message: str = "Test alert from CryptoQuant system"


class StartTraderRequest(BaseModel):
    name: str = "default"
    strategy: str = "rsi_mean_reversion"
    symbol: str = "BTCUSDT"
    leverage: int = Field(default=3, ge=1, le=125)
    interval_seconds: int = Field(default=60, ge=10, le=3600)


class OptimizeRequest(BacktestRequest):
    param_grid: dict = {}


# ── Global state (lazy-initialized) ──
_data_store = None
_collector = None
_simulator = None
_alert_manager = None
active_bots: dict = {}
_live_client = None


def _get_store():
    global _data_store
    if _data_store is None:
        try:
            cfg = _import_config()
            DataStore = _import_store()
            _data_store = DataStore(cfg['get_db_path']())
        except Exception as e:
            logger.warning(f"Cannot initialize DataStore: {e}")
            return None
    return _data_store


def _get_collector():
    global _collector
    if _collector is None:
        try:
            cfg = _import_config()
            exchange_cfg = cfg['get_exchange_config']()
            MarketDataCollector = _import_collector()
            _collector = MarketDataCollector(
                _get_store(),
                testnet=exchange_cfg.get("testnet", True),
                exchange_id=exchange_cfg.get("id", "binance"),
            )
        except Exception as e:
            logger.warning(f"Cannot initialize MarketDataCollector: {e}")
            return None
    return _collector


def _get_simulator():
    global _simulator
    if _simulator is None:
        try:
            cfg = _import_config()
            bt = cfg['get_backtest_config']()
            risk = cfg['get_risk_config']()
            RiskLimits = _import_risk_limits()
            PaperTradingSimulator = _import_simulator()
            limits = RiskLimits(
                max_position_pct=risk.get("max_position_pct", 0.3),
                max_total_position_pct=risk.get("max_total_position_pct", 0.8),
                max_daily_loss_pct=risk.get("max_daily_loss_pct", 0.05),
                max_consecutive_losses=risk.get("max_consecutive_losses", 3),
                stop_loss_pct=risk.get("stop_loss_pct", 0.05),
                take_profit_pct=risk.get("take_profit_pct", 0.10),
                position_sizing=risk.get("position_sizing", "fixed"),
            )
            _simulator = PaperTradingSimulator(
                initial_capital=bt.get("initial_capital", 10000),
                risk_limits=limits,
            )
        except Exception as e:
            logger.warning(f"Cannot initialize PaperTradingSimulator: {e}")
            return None
    return _simulator


def _get_live_client():
    """Get the live trading client for the currently active exchange."""
    global _live_client
    if _live_client is not None:
        return _live_client

    try:
        from execution.client import MultiExchangeClient
    except ImportError:
        logger.warning("MultiExchangeClient not available")
        return None

    try:
        cfg = _import_config()
        ex_id = cfg['get_exchange_id']()

        if ex_id == 'okx':
            exch_cfg = cfg['get_okx_config']()
            api_key = exch_cfg.get("api_key", "")
            api_secret = exch_cfg.get("api_secret", "")
            password = exch_cfg.get("password", "")
            testnet = exch_cfg.get("testnet", True)
        else:
            exch_cfg = cfg['get_binance_config']()
            api_key = exch_cfg.get("api_key", "")
            api_secret = exch_cfg.get("api_secret", "")
            password = ""
            testnet = exch_cfg.get("testnet", True)

        if api_key and api_secret:
            _live_client = MultiExchangeClient(
                exchange_id=ex_id,
                api_key=api_key,
                api_secret=api_secret,
                password=password,
                testnet=testnet,
            )
    except Exception as e:
        logger.warning(f"Cannot initialize live client: {e}")
        return None

    return _live_client


def _get_alert_manager():
    global _alert_manager
    if _alert_manager is None:
        try:
            cfg = _import_config()
            alerts_cfg = cfg['get_alerts_config']()
            AlertManager = _import_alert_manager()
            _alert_manager = AlertManager(
                bot_token=alerts_cfg.get("telegram_bot_token", ""),
                chat_id=alerts_cfg.get("telegram_chat_id", ""),
                enabled=alerts_cfg.get("enabled", False),
            )
        except Exception as e:
            logger.warning(f"Cannot initialize AlertManager: {e}")
            return None
    return _alert_manager


def _mask_key(key: str) -> str:
    """Mask API key showing only first 4 and last 4 characters."""
    if not key or len(key) <= 8:
        return "****"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ── Helpers ──

def _parse_symbols() -> List[dict]:
    """Build symbol list from config."""
    try:
        cfg = _import_config()
        symbols = cfg['get_trading_symbols']()
    except Exception:
        symbols = ["BTCUSDT", "ETHUSDT"]
    return [
        {"symbol": s, "base": s.replace("USDT", ""), "quote": "USDT"}
        for s in symbols
    ]


@functools.lru_cache(maxsize=1)
def _generate_sample_klines(limit: int = 500):
    """Generate sample OHLCV data for demo/testing (no numpy dependency)."""
    import random
    random.seed(42)
    base = 50000.0
    data = []
    now = datetime.now()

    for i in range(limit):
        t = now - timedelta(hours=limit - i)
        change = (random.random() - 0.5) * 400
        base += change
        open_p = base
        close_p = base + (random.random() - 0.5) * 300
        high_p = max(open_p, close_p) + abs(random.random() * 200)
        low_p = min(open_p, close_p) - abs(random.random() * 200)
        volume = abs(random.random() * 100 + 500)

        data.append({
            'timestamp': int(t.timestamp() * 1000),
            'open': round(open_p, 2),
            'high': round(high_p, 2),
            'low': round(low_p, 2),
            'close': round(close_p, 2),
            'volume': round(volume, 2),
        })

    return data


def _fetch_binance_history(symbol: str, interval: str, days: int = 90) -> Optional[list]:
    """
    Fetch historical klines directly from Binance public REST API.
    No ccxt dependency — uses urllib/requests.
    Returns list of kline dicts with timestamp/open/high/low/close/volume.
    """
    import requests
    import time

    interval_map = {
        '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1h': '1h', '4h': '4h', '1d': '1d', '1w': '1w',
    }
    binance_interval = interval_map.get(interval, '1h')

    # Binance API: max 1000 candles per request, we paginate
    all_klines = []
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 86400 * 1000
    limit = 1000
    max_pages = max(1, (days * 24 * 60) // (60 * limit)) + 5  # rough estimate

    for page in range(max_pages):
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={binance_interval}"
            f"&limit={limit}"
        )
        if page > 0 and all_klines:
            # Use the earliest timestamp we have as end_time
            end_time = all_klines[0][0] - 1
            url += f"&endTime={end_time}"
        elif page == 0:
            url += f"&endTime={end_time}"

        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Binance API returned {resp.status_code}: {resp.text[:200]}")
                break
            klines = resp.json()
            if not klines or not isinstance(klines, list):
                break

            # Prepend to maintain chronological order
            all_klines = klines + all_klines

            # Check if we've fetched enough data (earliest candle < start_time)
            if klines[0][0] <= start_time:
                break

            if len(klines) < limit:
                break

        except Exception as e:
            logger.warning(f"Binance history fetch error: {e}")
            break

    if not all_klines:
        return None

    # Convert to our standard format
    result = []
    for k in all_klines:
        result.append({
            'timestamp': int(k[0]),
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
        })
    return result


# ============================================================
# Health Endpoint
# ============================================================

@router.get("/health")
async def health_check():
    """Health check for frontend to detect backend status."""
    try:
        cfg = _import_config()
        mode = cfg['get_mode']()
    except Exception:
        mode = "unknown"

    return {
        "status": "ok",
        "mode": mode,
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# Market Data Endpoints
# ============================================================

@router.get("/market/ticker")
async def get_ticker(symbol: str = Query(default=None)):
    """Get current ticker for a symbol (depends on ccxt)."""
    try:
        if symbol is None:
            cfg = _import_config()
            symbol = cfg['get_trading_config']().get("default_symbol", "BTCUSDT")

        c = _get_collector()
        if c is None:
            return {
                "symbol": symbol,
                "last": None,
                "bid": None,
                "ask": None,
                "high": None,
                "low": None,
                "volume": None,
                "change_pct": None,
                "_demo": True,
                "_warning": "Market collector unavailable (ccxt may not be installed)",
            }

        ticker = c.get_ticker(symbol)
        if not ticker:
            return {
                "symbol": symbol,
                "last": None,
                "bid": None,
                "ask": None,
                "high": None,
                "low": None,
                "volume": None,
                "change_pct": None,
                "_demo": True,
            }
        return ticker
    except Exception as e:
        logger.error(f"Error in get_ticker: {e}")
        return {"error": "无法获取行情数据", "detail": str(e)}


@router.get("/market/klines")
async def get_klines(
    symbol: str = Query(default=None),
    interval: str = "1h",
    limit: int = 500,
    fetch: bool = False,
):
    """Get OHLCV kline data."""
    try:
        if symbol is None:
            cfg = _import_config()
            symbol = cfg['get_trading_config']().get("default_symbol", "BTCUSDT")

        store = _get_store()

        if fetch:
            c = _get_collector()
            if c is not None:
                df = c.fetch_and_store(symbol, interval, limit=limit)
            else:
                return _generate_sample_klines(limit)
        else:
            if store is not None:
                df = store.load_ohlcv(symbol, interval, limit=limit)
            else:
                return _generate_sample_klines(limit)

        if df is None:
            return _generate_sample_klines(limit)

        try:
            df_empty = df.empty
        except Exception:
            return _generate_sample_klines(limit)

        if df_empty:
            return _generate_sample_klines(limit)

        # Ensure timestamp column exists
        if 'open_time' in df.columns:
            df['timestamp'] = df['open_time']
        elif 'timestamp' not in df.columns:
            return _generate_sample_klines(limit)

        # Convert timestamp to ms int if needed
        try:
            pd, _ = _import_pandas_numpy()
            if not pd.api.types.is_integer_dtype(df['timestamp']):
                df['timestamp'] = pd.to_datetime(df['timestamp']).astype('int64') // 10**6
            else:
                # Already int, check if needs ms conversion (nanosecond to millisecond)
                sample = df['timestamp'].iloc[0] if len(df) > 0 else 0
                if sample > 1e15:
                    df['timestamp'] = df['timestamp'] // 10**6
        except Exception:
            pass

        return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    except Exception as e:
        logger.error(f"Error in get_klines: {e}")
        return {"error": "无法获取K线数据", "detail": str(e)}


@router.get("/market/symbols")
async def get_symbols():
    """Get available trading symbols."""
    try:
        return {"symbols": _parse_symbols()}
    except Exception as e:
        return {"error": "无法获取交易对列表", "detail": str(e)}


# ============================================================
# Exchange Info Endpoint
# ============================================================

@router.get("/exchange/info")
async def get_exchange_info():
    """Return exchange configuration and available symbols."""
    try:
        cfg = _import_config()
        exchange_cfg = cfg['get_exchange_config']()
        exchange_id = exchange_cfg.get("id", "binance")

        c = _get_collector()
        markets = {}

        if c is not None:
            try:
                markets = c.exchange.load_markets()
            except Exception as e:
                logger.warning(f"Failed to load markets from {exchange_id}: {e}")

        # Filter for USDT perpetual/futures pairs
        available_symbols = [
            s for s in markets.keys()
            if s.endswith("USDT") and (markets[s].get("swap") or markets[s].get("future") or markets[s].get("linear"))
        ]
        if not available_symbols:
            available_symbols = [s for s in markets.keys() if s.endswith("USDT")]
        if not available_symbols:
            available_symbols = cfg['get_trading_symbols']()

        return {
            "exchange_id": exchange_id,
            "testnet": exchange_cfg.get("testnet", True),
            "available_symbols": available_symbols[:50],
            "total_symbols": len(available_symbols),
        }
    except Exception as e:
        logger.error(f"Error in get_exchange_info: {e}")
        return {"error": "无法获取交易所信息", "detail": str(e)}


# ============================================================
# Strategy Endpoints
# ============================================================

@router.get("/strategies")
async def list_strategies():
    """List all registered strategies."""
    try:
        StrategyRegistry = _import_strategy_registry()
        return {'strategies': StrategyRegistry.list_strategies()}
    except Exception as e:
        return {"error": "无法获取策略列表", "detail": str(e)}


@router.get("/strategies/{name}")
async def get_strategy_info(name: str):
    """Get strategy details."""
    try:
        StrategyRegistry = _import_strategy_registry()
        strategy_cls = StrategyRegistry.get(name)
        if not strategy_cls:
            raise HTTPException(404, f"Strategy '{name}' not found")
        return {
            'name': name,
            'description': strategy_cls.__doc__ or "",
            'parameters': strategy_cls.get_param_info(),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"error": "无法获取策略详情", "detail": str(e)}


@router.post("/strategies/reload")
async def reload_strategies():
    """Hot-reload all strategy modules."""
    try:
        from strategy.manager import get_strategy_manager
        manager = get_strategy_manager()
        loaded, errors = manager.hot_reload_all()
        StrategyRegistry = _import_strategy_registry()
        strategies = StrategyRegistry.list_strategies()
        return {
            "success": len(errors) == 0,
            "reloaded": [s['name'] for s in strategies],
            "count": len(strategies),
            "errors": errors,
        }
    except Exception as e:
        return {"error": "无法重载策略", "detail": str(e)}


# ============================================================
# Backtest Endpoints
# ============================================================

def _load_backtest_data(request, cfg):
    """Load OHLCV data for backtest, falling back to sample data.
    
    Priority:
    1. Local SQLite database (previously cached data)
    2. Binance public REST API (no API key needed, no ccxt)
    3. ccxt collector (if available)
    4. Sample/generated data (last resort)
    """
    store = _get_store()
    symbol = request.symbol
    interval = request.interval

    fetch_limit = 50000 if (request.date_start or request.date_end) else 5000

    df = None
    if store is not None:
        df = store.load_ohlcv(symbol, interval, limit=fetch_limit)

    # If no data, try Binance public API directly (no ccxt dependency)
    if (df is None or (hasattr(df, 'empty') and df.empty)) and request.days:
        try:
            raw_data = _fetch_binance_history(
                symbol, interval, days=max(request.days, 90)
            )
            if raw_data:
                pd_mod, np_mod = _import_pandas_numpy()
                df = pd_mod.DataFrame(raw_data)
                df['timestamp'] = pd_mod.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                # Save to local DB for future use
                if store is not None:
                    store.save_ohlcv(symbol, interval, df)
                logger.info(f"Fetched {len(df)} {interval} candles for {symbol} from Binance API")
        except Exception as e:
            logger.warning(f"Binance API history fetch failed: {e}")

    # Fallback: try ccxt collector
    if (df is None or (hasattr(df, 'empty') and df.empty)) and request.days:
        c = _get_collector()
        if c is not None:
            try:
                df = c.fetch_history(symbol, interval, days=max(request.days, 730))
            except Exception:
                pass

    # Filter by date range if we have data
    if df is not None and not (hasattr(df, 'empty') and df.empty):
        try:
            pd_mod, _ = _import_pandas_numpy()
            if request.date_start:
                df = df[df.index >= pd_mod.Timestamp(request.date_start)]
            if request.date_end:
                df = df[df.index <= pd_mod.Timestamp(request.date_end) + pd_mod.Timedelta(days=1)]
        except Exception:
            pass

    # Last resort: sample data
    if df is None or (hasattr(df, 'empty') and df.empty):
        sample = _generate_sample_klines(1000)
        pd_mod, _ = _import_pandas_numpy()
        df = pd_mod.DataFrame(sample)
        df['timestamp'] = pd_mod.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

    return df


def _run_backtest_core(strategy_name, symbol, interval, initial_capital, params, df):
    """Core backtest execution, shared across endpoints."""
    cfg = _import_config()
    bt_cfg = cfg['get_backtest_config']()
    trading_cfg = cfg['get_trading_config']()
    risk_cfg = cfg['get_risk_config']()
    StrategyRegistry = _import_strategy_registry()
    BacktestEngine = _import_backtest_engine()

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    if 'leverage' not in params:
        params['leverage'] = trading_cfg.get('default_leverage', 3)

    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission=bt_cfg.get('commission', 0.0004),
        slippage=bt_cfg.get('slippage', 0.0001),
        position_pct=risk_cfg.get('max_position_pct', 0.3),
        default_leverage=trading_cfg.get('default_leverage', 3),
    )
    strategy = strategy_cls(params)
    result = engine.run(strategy, df, symbol)
    return result


@router.post("/backtest/compare")
async def run_backtest_compare(request: BacktestRequest):
    """Run all strategies on the same data and return comparison."""
    try:
        cfg = _import_config()
        df = _load_backtest_data(request, cfg)
        StrategyRegistry = _import_strategy_registry()

        symbol = request.symbol
        interval = request.interval

        def _run_one(name):
            try:
                result = _run_backtest_core(
                    name, symbol, interval,
                    request.initial_capital or cfg['get_backtest_config']().get('initial_capital', 10000),
                    request.params, df
                )
                return {
                    'name': name,
                    'trades': result['metrics']['total_trades'],
                    'win_rate': result['metrics']['win_rate'],
                    'total_return': result['metrics']['total_return'],
                    'sharpe_ratio': result['metrics']['sharpe_ratio'],
                    'max_drawdown': result['metrics']['max_drawdown'],
                    'profit_factor': result['metrics']['profit_factor'],
                }
            except Exception as e:
                return {'name': name, 'error': str(e)[:80]}

        loop = asyncio.get_running_loop()
        strategy_names = [s['name'] for s in StrategyRegistry.list_strategies()]
        tasks = [loop.run_in_executor(_backtest_compare_executor, _run_one, name) for name in strategy_names]
        results = await asyncio.gather(*tasks)

        return {
            'symbol': symbol, 'interval': interval,
            'candles': len(df),
            'date_start': str(df.index[0].date()), 'date_end': str(df.index[-1].date()),
            'results': list(results),
        }
    except Exception as e:
        logger.error(f"Error in backtest compare: {e}")
        return {"error": "回测对比失败", "detail": str(e)}


@router.post("/backtest/optimize")
async def run_backtest_optimize(request: OptimizeRequest):
    """Run parameter optimization."""
    try:
        param_grid = request.param_grid
        if not param_grid:
            raise HTTPException(400, "param_grid is required")

        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))

        if len(combinations) > 50:
            raise HTTPException(400, f"Too many combinations: {len(combinations)} (max 50)")

        cfg = _import_config()
        StrategyRegistry = _import_strategy_registry()
        strategy_cls = StrategyRegistry.get(request.strategy)
        if not strategy_cls:
            raise HTTPException(400, f"Unknown strategy: {request.strategy}")

        df = _load_backtest_data(request, cfg)
        trading_cfg = cfg['get_trading_config']()

        results = []
        for combo in combinations:
            params = dict(zip(keys, combo))
            if 'leverage' not in params:
                params['leverage'] = trading_cfg.get('default_leverage', 3)
            try:
                result = _run_backtest_core(
                    request.strategy, request.symbol, request.interval,
                    request.initial_capital or cfg['get_backtest_config']().get('initial_capital', 10000),
                    params, df
                )
                results.append({
                    'params': params,
                    'sharpe_ratio': result['metrics']['sharpe_ratio'],
                    'total_return': result['metrics']['total_return'],
                    'max_drawdown': result['metrics']['max_drawdown'],
                    'win_rate': result['metrics']['win_rate'],
                    'profit_factor': result['metrics']['profit_factor'],
                    'total_trades': result['metrics']['total_trades'],
                    'final_capital': result['final_capital'],
                })
            except Exception as e:
                results.append({'params': params, 'error': str(e)[:80]})

        valid = [r for r in results if 'error' not in r]
        errors = [r for r in results if 'error' in r]
        valid.sort(key=lambda r: r['sharpe_ratio'] or -999, reverse=True)
        top10 = valid[:10]

        return {
            'strategy': request.strategy,
            'symbol': request.symbol,
            'interval': request.interval,
            'total_combinations': len(combinations),
            'best_params': top10[0]['params'] if top10 else None,
            'best_sharpe': top10[0]['sharpe_ratio'] if top10 else None,
            'top10': top10,
            'errors': errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in backtest optimize: {e}")
        return {"error": "回测优化失败", "detail": str(e)}


@router.post("/backtest")
async def run_backtest(request: BacktestRequest):
    """Run a backtest."""
    try:
        cfg = _import_config()
        result = _run_backtest_core(
            request.strategy, request.symbol, request.interval,
            request.initial_capital or cfg['get_backtest_config']().get('initial_capital', 10000),
            request.params,
            _load_backtest_data(request, cfg)
        )

        df = _load_backtest_data(request, cfg)
        actual_start = str(df.index[0].date())
        actual_end = str(df.index[-1].date())

        # Format equity curve
        equity = result['equity_curve'].reset_index()
        try:
            pd, _ = _import_pandas_numpy()
            equity['timestamp'] = pd.to_datetime(equity['timestamp']).astype('int64') // 10**6
        except Exception:
            equity['timestamp'] = equity['timestamp'].astype(int) // 10**6

        # Format trades
        trades_list = []
        if not result['trades'].empty:
            for _, t in result['trades'].iterrows():
                trades_list.append({
                    'entry_time': str(t.get('entry_time', '')),
                    'exit_time': str(t.get('exit_time', '')),
                    'side': t.get('side', ''),
                    'entry_price': float(t.get('entry_price', 0)),
                    'exit_price': float(t.get('exit_price', 0)),
                    'pnl': float(t.get('pnl', 0)),
                    'pnl_pct': float(t.get('pnl_pct', 0)),
                })

        return {
            'strategy': request.strategy,
            'symbol': request.symbol,
            'interval': request.interval,
            'params': request.params,
            'metrics': result['metrics'],
            'equity_curve': equity.to_dict('records'),
            'trades': trades_list,
            'initial_capital': request.initial_capital or cfg['get_backtest_config']().get('initial_capital', 10000),
            'final_capital': result['final_capital'],
            'date_start': actual_start,
            'date_end': actual_end,
            'candles': len(df),
        }
    except Exception as e:
        logger.error(f"Error in backtest: {e}")
        return {"error": "回测执行失败", "detail": str(e)}


# ============================================================
# Backtest Export Endpoints
# ============================================================

def _run_backtest_for_export(request: BacktestRequest):
    """Shared backtest runner for export endpoints."""
    cfg = _import_config()
    df = _load_backtest_data(request, cfg)
    result = _run_backtest_core(
        request.strategy, request.symbol, request.interval,
        request.initial_capital or cfg['get_backtest_config']().get('initial_capital', 10000),
        request.params, df
    )
    actual_start = str(df.index[0].date())
    actual_end = str(df.index[-1].date())
    result['date_start'] = actual_start
    result['date_end'] = actual_end
    result['params'] = request.params
    result['interval'] = request.interval
    return result


@router.post("/backtest/export")
async def export_backtest_csv(request: BacktestRequest):
    """Run backtest and return trades as a CSV download."""
    try:
        result = _run_backtest_for_export(request)

        output = io.StringIO()
        output.write("entry_time,exit_time,side,entry_price,exit_price,pnl,pnl_pct,return_cumulative\n")

        if not result['trades'].empty:
            cumulative = 1.0
            for _, t in result['trades'].iterrows():
                pnl_pct = float(t.get('pnl_pct', 0))
                cumulative *= (1 + pnl_pct / 100)
                return_cumulative = (cumulative - 1) * 100
                output.write(
                    f"{t.get('entry_time', '')},{t.get('exit_time', '')},"
                    f"{t.get('side', '')},{t.get('entry_price', 0)},"
                    f"{t.get('exit_price', 0)},{t.get('pnl', 0)},"
                    f"{pnl_pct},{return_cumulative:.4f}\n"
                )

        csv_content = output.getvalue()
        output.close()

        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"backtest_{request.symbol}_{request.strategy}_{date_str}.csv"

        return StreamingResponse(
            io.BytesIO(csv_content.encode('utf-8')),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"Error in CSV export: {e}")
        return {"error": "CSV导出失败", "detail": str(e)}


@router.post("/backtest/export/pdf")
async def export_backtest_pdf(request: BacktestRequest):
    """Run backtest and return a PDF report."""
    try:
        result = _run_backtest_for_export(request)
        m = result['metrics']

        FPDF = _import_fpdf()
        pdf = FPDF()
        pdf.add_page()

        # Title
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, f"Backtest Report: {request.symbol} / {request.strategy}", ln=True, align="C")
        pdf.ln(4)

        # Date range and config
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Date Range: {result['date_start']}  to  {result['date_end']}", ln=True)
        pdf.cell(0, 6, f"Interval: {request.interval}    Strategy: {request.strategy}    Symbol: {request.symbol}", ln=True)
        pdf.ln(6)

        # Metrics table
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Performance Metrics", ln=True)
        pdf.ln(2)

        metrics_rows = [
            ("Total Return", f"{m.get('total_return', 0):.2f}%"),
            ("Annual Return", f"{m.get('annual_return', 0):.2f}%"),
            ("Sharpe Ratio", f"{m.get('sharpe_ratio', 0):.2f}"),
            ("Win Rate", f"{m.get('win_rate', 0):.2f}%"),
            ("Max Drawdown", f"{m.get('max_drawdown', 0):.2f}%"),
            ("Total Trades", str(m.get('total_trades', 0))),
            ("Profit Factor", f"{m.get('profit_factor', 0):.2f}"),
            ("Calmar Ratio", f"{m.get('calmar_ratio', 0):.2f}"),
        ]

        pdf.set_font("Helvetica", "", 10)
        col_w = 50
        for label, value in metrics_rows:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(col_w, 7, label, border=1)
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(col_w, 7, value, border=1)
            pdf.ln()

        pdf.ln(6)

        # Equity curve (ASCII style)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Equity Curve (ASCII)", ln=True)
        pdf.ln(2)

        equity = result['equity_curve']
        if len(equity) > 0:
            eq_values = equity['equity'].values
            eq_min, eq_max = eq_values.min(), eq_values.max()
            chart_width = 80
            chart_height = 15

            pdf.set_font("Courier", "", 7)
            step = max(1, len(eq_values) // chart_width)
            for row in range(chart_height, -1, -1):
                line = ""
                for i in range(0, len(eq_values), step):
                    if eq_max == eq_min:
                        val = chart_height // 2
                    else:
                        val = int((eq_values[i] - eq_min) / (eq_max - eq_min) * chart_height)
                    line += "#" if val >= row else " "
                pdf.cell(0, 4, line, ln=True)

            pdf.set_font("Helvetica", "", 8)
            pdf.cell(0, 5, f"Min: ${eq_min:,.0f}    Max: ${eq_max:,.0f}", ln=True)

        pdf.ln(6)

        # Trade summary
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, "Trade List", ln=True)
        pdf.ln(2)

        if not result['trades'].empty:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(32, 6, "Entry", border=1)
            pdf.cell(32, 6, "Exit", border=1)
            pdf.cell(14, 6, "Side", border=1)
            pdf.cell(22, 6, "Entry $", border=1)
            pdf.cell(22, 6, "Exit $", border=1)
            pdf.cell(22, 6, "PnL", border=1)
            pdf.cell(22, 6, "PnL %", border=1)
            pdf.ln()

            pdf.set_font("Helvetica", "", 8)
            for _, t in result['trades'].iterrows():
                entry_str = str(t.get('entry_time', ''))[:10]
                exit_str = str(t.get('exit_time', ''))[:10]
                pdf.cell(32, 5, entry_str, border=1)
                pdf.cell(32, 5, exit_str, border=1)
                pdf.cell(14, 5, str(t.get('side', '')), border=1)
                pdf.cell(22, 5, f"{t.get('entry_price', 0):.2f}", border=1)
                pdf.cell(22, 5, f"{t.get('exit_price', 0):.2f}", border=1)
                pdf.cell(22, 5, f"{t.get('pnl', 0):.2f}", border=1)
                pdf.cell(22, 5, f"{t.get('pnl_pct', 0):.2f}%", border=1)
                pdf.ln()

        pdf_content = pdf.output()
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"backtest_{request.symbol}_{request.strategy}_{date_str}.pdf"

        return StreamingResponse(
            io.BytesIO(pdf_content),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"Error in PDF export: {e}")
        return {"error": "PDF导出失败", "detail": str(e)}


# ============================================================
# Trading Endpoints
# ============================================================

@router.get("/account")
async def get_account():
    """Get account summary."""
    try:
        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}
        return sim.get_account_summary()
    except Exception as e:
        return {"error": "无法获取账户信息", "detail": str(e)}


@router.post("/trade/open")
async def open_trade(request: TradeRequest):
    """Open a new trade."""
    try:
        symbol = request.symbol
        side = request.side
        leverage = request.leverage

        c = _get_collector()
        if c is None:
            return {"error": "行情采集器不可用", "detail": "MarketDataCollector not available (ccxt may be missing)"}

        price = c.get_current_price(symbol)
        if not price:
            return {"error": "无法获取价格", "detail": f"Cannot get price for {symbol}"}

        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}

        order = sim.open_position(symbol, side, price, leverage)
        if not order:
            return {"error": "交易被风控拒绝", "detail": "Trade rejected by risk manager"}

        # Fire trade alert
        am = _get_alert_manager()
        if am is not None:
            try:
                am.send_trade_alert(symbol, side, price)
            except Exception:
                pass

        # Broadcast account update via WebSocket
        ws = _import_ws_manager()
        if ws is not None:
            try:
                await ws.broadcast_account(sim.get_account_summary())
            except Exception:
                pass

        return {'success': True, 'order': order}
    except Exception as e:
        logger.error(f"Error in open_trade: {e}")
        return {"error": "开仓失败", "detail": str(e)}


@router.post("/trade/close")
async def close_trade(request: CloseTradeRequest):
    """Close a position."""
    try:
        symbol = request.symbol

        c = _get_collector()
        if c is None:
            return {"error": "行情采集器不可用", "detail": "MarketDataCollector not available (ccxt may be missing)"}

        price = c.get_current_price(symbol)
        if not price:
            return {"error": "无法获取价格", "detail": f"Cannot get price for {symbol}"}

        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}

        order = sim.close_position(symbol, price, "手动平仓")
        if not order:
            return {"error": "未找到持仓", "detail": f"No position for {symbol}"}

        # Fire trade alert with PnL
        am = _get_alert_manager()
        if am is not None:
            try:
                pnl = order.get('pnl', 0) if isinstance(order, dict) else 0
                side = order.get('side', '') if isinstance(order, dict) else ''
                am.send_trade_alert(symbol, side, price, pnl)
            except Exception:
                pass

        # Broadcast account update via WebSocket
        ws = _import_ws_manager()
        if ws is not None:
            try:
                await ws.broadcast_account(sim.get_account_summary())
            except Exception:
                pass

        return {'success': True, 'order': order}
    except Exception as e:
        logger.error(f"Error in close_trade: {e}")
        return {"error": "平仓失败", "detail": str(e)}


@router.get("/trade/history")
async def get_trade_history(limit: int = 50):
    """Get trade history."""
    try:
        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}

        history = [o for o in sim.order_history if o.get('side') == 'CLOSE']
        return {
            'trades': history[-limit:],
            'total': len(history),
        }
    except Exception as e:
        return {"error": "无法获取交易历史", "detail": str(e)}


# ============================================================
# Risk Management Endpoints
# ============================================================

@router.get("/risk/summary")
async def get_risk_summary():
    """Get risk management summary."""
    try:
        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}
        return sim.risk_manager.get_risk_summary()
    except Exception as e:
        return {"error": "无法获取风控摘要", "detail": str(e)}


@router.post("/risk/limits")
async def update_risk_limits(limits: RiskLimitsRequest):
    """Update risk limits."""
    try:
        sim = _get_simulator()
        if sim is None:
            return {"error": "模拟交易引擎未初始化", "detail": "PaperTradingSimulator not available"}

        rm = sim.risk_manager
        if limits.max_position_pct is not None:
            rm.limits.max_position_pct = limits.max_position_pct
        if limits.max_daily_loss_pct is not None:
            rm.limits.max_daily_loss_pct = limits.max_daily_loss_pct
        if limits.stop_loss_pct is not None:
            rm.limits.stop_loss_pct = limits.stop_loss_pct
        if limits.take_profit_pct is not None:
            rm.limits.take_profit_pct = limits.take_profit_pct

        return {'success': True, 'limits': {
            'max_position_pct': rm.limits.max_position_pct,
            'max_daily_loss_pct': rm.limits.max_daily_loss_pct,
            'stop_loss_pct': rm.limits.stop_loss_pct,
            'take_profit_pct': rm.limits.take_profit_pct,
        }}
    except Exception as e:
        return {"error": "无法更新风控限制", "detail": str(e)}


# ============================================================
# Mode Endpoints
# ============================================================

@router.get("/mode")
async def get_current_mode():
    """Return current trading mode."""
    try:
        cfg = _import_config()
        return {'mode': cfg['get_mode']()}
    except Exception as e:
        return {"error": "无法获取交易模式", "detail": str(e)}


@router.post("/mode")
async def set_mode(request: ModeRequest):
    """Switch trading mode between paper and live."""
    try:
        if request.mode == "live":
            cfg = _import_config()
            binance_cfg = cfg['get_binance_config']()
            api_key = binance_cfg.get("api_key", "")
            api_secret = binance_cfg.get("api_secret", "")
            if not api_key or not api_secret:
                return {"error": "实盘模式需要配置API密钥", "detail": "Live mode requires API keys configured"}

            client = _get_live_client()
            if client is None:
                return {"error": "无法初始化交易所客户端", "detail": "Cannot initialize exchange client (ccxt may be missing)"}

            try:
                client.is_connected()
            except Exception as e:
                return {"error": "交易所连接测试失败", "detail": str(e)}

        cfg = _import_config()
        config = cfg['get_config']()
        config["mode"] = request.mode
        return {'success': True, 'mode': request.mode}
    except Exception as e:
        logger.error(f"Error in set_mode: {e}")
        return {"error": "切换模式失败", "detail": str(e)}


# ============================================================
# Alert Endpoints
# ============================================================

@router.post("/alerts/test")
async def test_alert(request: AlertTestRequest):
    """Send a test message via Telegram."""
    try:
        cfg = _import_config()
        alerts_cfg = cfg['get_alerts_config']()
        am = _get_alert_manager()
        if am is None:
            return {"error": "告警管理器不可用", "detail": "AlertManager not available"}

        success = am.send_telegram(
            message=request.message,
            bot_token=alerts_cfg.get("telegram_bot_token", ""),
            chat_id=alerts_cfg.get("telegram_chat_id", ""),
        )
        if not success:
            return {"error": "Telegram发送失败", "detail": "Check bot token and chat ID"}
        return {'success': True, 'message': request.message}
    except Exception as e:
        return {"error": "告警测试失败", "detail": str(e)}


@router.post("/alerts/config")
async def update_alert_config(request: AlertConfigRequest):
    """Update alert configuration at runtime."""
    try:
        global _alert_manager
        am = _get_alert_manager()
        if am is not None:
            am.configure(request.bot_token, request.chat_id, request.enabled)

        cfg = _import_config()
        config = cfg['get_config']()
        if "alerts" not in config:
            config["alerts"] = {}
        config["alerts"]["telegram_bot_token"] = request.bot_token
        config["alerts"]["telegram_chat_id"] = request.chat_id
        config["alerts"]["enabled"] = request.enabled

        return {
            'success': True,
            'enabled': request.enabled,
            'chat_id': request.chat_id[:4] + "****" if request.chat_id else "",
        }
    except Exception as e:
        return {"error": "更新告警配置失败", "detail": str(e)}


# ============================================================
# System Endpoints
# ============================================================

@router.get("/system/status")
async def get_system_status():
    """Get system status."""
    try:
        cfg = _import_config()
        db_path = cfg['get_db_path']()
        scheduler = _import_scheduler()
        ws = _import_ws_manager()

        traders = scheduler.list_traders() if scheduler else []
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

        return {
            'mode': cfg['get_mode'](),
            'uptime': 'running',
            'active_bots': len(active_bots),
            'live_traders': len(traders),
            'trader_list': traders,
            'database_size': db_size,
            'ws_market': ws.market_connections if ws else 0,
            'ws_account': ws.account_connections if ws else 0,
        }
    except Exception as e:
        return {"error": "无法获取系统状态", "detail": str(e)}


# ============================================================
# Quick-Start Endpoint
# ============================================================

@router.post("/quick-start")
async def quick_start():
    """一键开箱：自动配置最优策略并启动模拟盘"""
    try:
        from execution.live_trader import LivePaperTrader
        from risk.manager import RiskManager, RiskLimits
        from strategy.rsi_mean_reversion import RSIMeanReversionStrategy
        from data.store import DataStore

        cfg = _import_config()
        trading = cfg['get_trading_config']()
        risk_cfg = cfg['get_risk_config']()

        symbol = trading.get("default_symbol", "BTCUSDT")
        interval = "1h"

        strategy = RSIMeanReversionStrategy(
            rsi_period=14,
            oversold=30,
            overbought=70,
            use_divergence=True,
        )

        limits = RiskLimits(
            max_position_pct=risk_cfg.get("max_position_pct", 0.3),
            max_total_position_pct=risk_cfg.get("max_total_position_pct", 0.8),
            max_daily_loss_pct=risk_cfg.get("max_daily_loss_pct", 0.05),
            max_consecutive_losses=risk_cfg.get("max_consecutive_losses", 3),
            stop_loss_pct=risk_cfg.get("stop_loss_pct", 0.05),
            take_profit_pct=risk_cfg.get("take_profit_pct", 0.10),
            position_sizing=risk_cfg.get("position_sizing", "fixed"),
        )

        simulator = PaperTradingSimulator(
            initial_capital=cfg['get_backtest_config']().get("initial_capital", 10000),
            risk_limits=limits,
        )

        store = DataStore(cfg['get_db_path']())
        trader = LivePaperTrader(
            strategy=strategy,
            symbol=symbol,
            simulator=simulator,
            store=store,
            interval_seconds=60,
        )

        bot_id = f"quick_{symbol}_{interval}"
        active_bots[bot_id] = trader

        import asyncio
        asyncio.create_task(trader.start())

        return {
            "success": True,
            "message": f"已启动 {symbol} {interval} RSI均值回归策略（模拟盘）",
            "bot_id": bot_id,
            "strategy": "RSI均值回归",
            "symbol": symbol,
            "interval": interval,
        }
    except ImportError as e:
        return {"error": "模块导入失败", "detail": str(e)}
    except Exception as e:
        return {"error": "一键启动失败", "detail": str(e)}


# ============================================================
# Live Status Endpoint
# ============================================================

@router.get("/live/status")
async def get_live_status():
    """Get status of all live trading bots."""
    try:
        bots_status = {}
        for bot_id, trader in active_bots.items():
            try:
                if hasattr(trader, 'status'):
                    bots_status[bot_id] = trader.status
                else:
                    bots_status[bot_id] = {"running": trader.is_running if hasattr(trader, 'is_running') else False}
            except Exception:
                bots_status[bot_id] = {"running": False}

        return {
            "bots": bots_status,
            "count": len(bots_status),
            "running_count": sum(1 for b in bots_status.values() if b.get("running", False)),
        }
    except Exception as e:
        return {"error": "无法获取实盘状态", "detail": str(e)}


# ============================================================
# Live Stop Endpoint
# ============================================================

@router.post("/live/stop")
async def stop_live_trader(request: dict):
    """Stop a live paper trading bot."""
    try:
        bot_id = request.get("name") or request.get("bot_id", "")
        if not bot_id:
            return {"error": "缺少bot_id", "detail": "bot_id or name is required"}

        trader = active_bots.get(bot_id)
        if not trader:
            return {"error": "未找到机器人", "detail": f"Bot '{bot_id}' not found"}

        try:
            if hasattr(trader, 'stop'):
                await trader.stop()
            del active_bots[bot_id]
            return {"success": True, "bot_id": bot_id}
        except Exception as e:
            return {"error": "停止失败", "detail": str(e)}
    except Exception as e:
        return {"error": "停止操作失败", "detail": str(e)}


# ============================================================
# Live Trading Endpoints (via TradingScheduler)
# ============================================================

@router.post("/live/start")
async def start_live_trader(request: StartTraderRequest):
    """Start a live paper trading bot via the scheduler."""
    try:
        scheduler = _import_scheduler()
        if scheduler is None:
            return {"error": "调度器不可用", "detail": "TradingScheduler not available"}

        await scheduler.start_trader(
            name=request.name,
            strategy_name=request.strategy,
            symbol=request.symbol,
            leverage=request.leverage,
            interval_seconds=request.interval_seconds,
        )
        return {"success": True, "name": request.name, "strategy": request.strategy}
    except Exception as e:
        return {"error": "启动实盘失败", "detail": str(e)}


@router.get("/live/reports")
async def get_live_reports():
    """Get latest trading reports from all bots."""
    try:
        scheduler = _import_scheduler()
        if scheduler is None:
            return {"error": "调度器不可用", "detail": "TradingScheduler not available"}

        reports = scheduler.get_all_reports()
        result = {}
        for name, report in reports.items():
            reporter = scheduler.get_reporter(name)
            text = reporter.format_text_report(report) if reporter else ""
            result[name] = {'json': report, 'text': text}
        return {'reports': result, 'count': len(result)}
    except Exception as e:
        return {"error": "无法获取实盘报告", "detail": str(e)}


@router.get("/live/report/{name}")
async def get_live_report(name: str):
    """Get detailed report for a specific trader."""
    try:
        scheduler = _import_scheduler()
        if scheduler is None:
            return {"error": "调度器不可用", "detail": "TradingScheduler not available"}

        reporter = scheduler.get_reporter(name)
        if not reporter:
            return {"error": "未找到交易员", "detail": f"Trader '{name}' not found"}

        report = reporter.get_latest_report()
        if not report:
            return {'name': name, 'report': None, 'message': 'No report generated yet'}
        text = reporter.format_text_report(report)
        return {'name': name, 'report': report, 'text': text}
    except Exception as e:
        return {"error": "无法获取交易员报告", "detail": str(e)}


# ============================================================
# Exchange Set-Key Endpoint (available from settings page)
# ============================================================
# Note: exchange/test and exchange/switch are in web/exchange_routes.py
# which is included via sub-router below.

class ExchangeKeyRequest(BaseModel):
    exchange_id: str
    api_key: str
    api_secret: str
    password: Optional[str] = None
    testnet: bool = True


@router.post("/exchange/set-key")
async def set_exchange_key(req: Request, request: ExchangeKeyRequest):
    """Set exchange API keys from settings page (not just onboarding)."""
    try:
        # Security: only allow localhost to set API keys
        client_host = req.client.host if req.client else None
        if client_host != "127.0.0.1":
            return {"error": "拒绝访问", "detail": "出于安全考虑，设置API密钥仅允许本地访问"}
        
        cfg = _import_config()
        config = cfg['get_config']()

        if request.exchange_id not in ("binance", "okx"):
            return {"error": "不支持的交易所", "detail": f"Exchange '{request.exchange_id}' not supported. Use 'binance' or 'okx'."}

        if request.exchange_id == "okx":
            if "okx" not in config:
                config["okx"] = {}
            config["okx"]["api_key"] = request.api_key
            config["okx"]["api_secret"] = request.api_secret
            config["okx"]["password"] = request.password or ""
            config["okx"]["testnet"] = request.testnet
        else:
            if "binance" not in config:
                config["binance"] = {}
            config["binance"]["api_key"] = request.api_key
            config["binance"]["api_secret"] = request.api_secret
            config["binance"]["testnet"] = request.testnet

        # Also update active exchange if switching
        if "exchange" not in config:
            config["exchange"] = {}
        config["exchange"]["id"] = request.exchange_id
        config["exchange"]["testnet"] = request.testnet

        return {
            "success": True,
            "exchange": request.exchange_id,
            "testnet": request.testnet,
            "message": f"{request.exchange_id} API密钥已保存",
            "api_key_masked": _mask_key(request.api_key),
        }
    except Exception as e:
        return {"error": "保存API密钥失败", "detail": str(e)}


# ============================================================
# WebSocket Endpoints
# ============================================================

@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    """Real-time market data stream (ticker updates)."""
    ws = _import_ws_manager()
    if ws is None:
        await websocket.close(code=1011, reason="WebSocket manager unavailable")
        return

    await ws.connect(websocket, "market")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws.disconnect(websocket, "market")


@router.websocket("/ws/account")
async def ws_account(websocket: WebSocket):
    """Real-time account updates (positions, equity changes)."""
    ws = _import_ws_manager()
    if ws is None:
        await websocket.close(code=1011, reason="WebSocket manager unavailable")
        return

    await ws.connect(websocket, "account")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws.disconnect(websocket, "account")


# ============================================================
# Sub-router registration (all under /api prefix)
# Each sub-router import is wrapped in try/except to survive
# individual module failures in Chaquopy.
# ============================================================

_sub_routers = [
    ("strategy_admin", "web.strategy_admin_routes"),
    ("backup", "web.backup_routes"),
    ("data_admin", "web.data_admin_routes"),
    ("alert_custom", "web.alert_routes"),
    ("note", "web.note_routes"),
    ("recommend", "web.recommend_routes"),
    ("exchange", "web.exchange_routes"),
]

for _name, _module_path in _sub_routers:
    try:
        _mod = __import__(_module_path, fromlist=["router"])
        router.include_router(_mod.router)
        logger.info(f"Registered sub-router: {_name}")
    except ImportError as e:
        logger.warning(f"Sub-router '{_name}' unavailable ({_module_path}): {e}")
    except Exception as e:
        logger.warning(f"Failed to register sub-router '{_name}': {e}")

# ============================================================
# Dashboard Snapshot Endpoint
# ============================================================

@router.get("/dashboard/snapshot")
async def dashboard_snapshot(symbol: str = Query(default="BTCUSDT"), interval: str = Query(default="1h")):
    """单次请求获取仪表盘全部数据"""
    try:
        import asyncio
        
        async def get_account():
            try:
                sim = _get_simulator()
                if sim is None: return {"total_equity": 0, "capital": 0, "positions": []}
                return sim.get_account_summary()
            except: return {"error": "account unavailable"}
        
        async def get_klines():
            try:
                store = _get_store()
                if store is None: return []
                df = store.load_ohlcv(symbol, interval, limit=200)
                if df is None or df.empty: return []
                return [{"time": str(i[0]), "open": float(i[1]), "high": float(i[2]), 
                         "low": float(i[3]), "close": float(i[4]), "volume": float(i[5])} 
                        for i in df.itertuples()][:200]
            except: return []
        
        account, klines = await asyncio.gather(get_account(), get_klines())
        return {"account": account, "klines": klines, "symbol": symbol, "interval": interval}
    except Exception as e:
        return {"error": str(e)}
