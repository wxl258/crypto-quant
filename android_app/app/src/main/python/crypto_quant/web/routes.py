"""
Web API Routes — FastAPI endpoints for the trading system
"""
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# 兼容 Pydantic v1 和 v2
try:
    from pydantic import field_validator
except ImportError:
    # Pydantic v1 fallback
    from pydantic import validator as field_validator
from typing import Optional, List, Literal
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import logging
import json
import os
import io
import itertools
from fpdf import FPDF

from config import (
    get_db_path, get_trading_symbols, get_trading_config,
    get_risk_config, get_backtest_config, get_mode, get_binance_config,
    get_alerts_config, get_exchange_config, get_config,
    get_exchange_id, get_okx_config,
)
from data.store import DataStore
from data.collector import MarketDataCollector
from strategy import StrategyRegistry
from backtest.engine import BacktestEngine
from execution.simulator import PaperTradingSimulator
from execution.scheduler import scheduler as trading_scheduler
from risk.manager import RiskLimits
from web.alerts import AlertManager
from web.websocket import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# ── Pydantic request models ──

class BacktestRequest(BaseModel):
    strategy: str = "dual_ma"
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    initial_capital: float = Field(default=10000, gt=0)
    params: dict = {}
    days: int = Field(default=90, ge=1, le=3650)
    date_start: Optional[str] = None   # "2024-01-01" format
    date_end: Optional[str] = None     # "2025-12-31" format

    @field_validator('symbol')
    @classmethod
    def validate_symbol_backtest(cls, v):
        allowed = get_trading_symbols()
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
        allowed = get_trading_symbols()
        if v not in allowed:
            raise ValueError(f"symbol must be one of {allowed}")
        return v


class CloseTradeRequest(BaseModel):
    symbol: str = "BTCUSDT"

    @field_validator('symbol')
    @classmethod
    def validate_symbol_close(cls, v):
        allowed = get_trading_symbols()
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

# ── Global state (in production, use proper dependency injection) ──
_data_store = None
_collector = None
_simulator = None
_alert_manager = None
active_bots: dict = {}
_live_client = None


def _get_store() -> DataStore:
    global _data_store
    if _data_store is None:
        _data_store = DataStore(get_db_path())
    return _data_store


def _get_collector() -> MarketDataCollector:
    global _collector
    if _collector is None:
        exchange_cfg = get_exchange_config()
        _collector = MarketDataCollector(
            _get_store(),
            testnet=exchange_cfg.get("testnet", True),
            exchange_id=exchange_cfg.get("id", "binance"),
        )
    return _collector


def _get_simulator() -> PaperTradingSimulator:
    global _simulator
    if _simulator is None:
        bt = get_backtest_config()
        risk = get_risk_config()
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
    return _simulator


def _get_live_client():
    """Get the live trading client for the currently active exchange."""
    global _live_client
    if _live_client is not None:
        return _live_client

    from execution.client import MultiExchangeClient

    ex_id = get_exchange_id()

    if ex_id == 'okx':
        cfg = get_okx_config()
        api_key = cfg.get("api_key", "")
        api_secret = cfg.get("api_secret", "")
        password = cfg.get("password", "")
        testnet = cfg.get("testnet", True)
    else:
        cfg = get_binance_config()
        api_key = cfg.get("api_key", "")
        api_secret = cfg.get("api_secret", "")
        password = ""
        testnet = cfg.get("testnet", True)

    if api_key and api_secret:
        _live_client = MultiExchangeClient(
            exchange_id=ex_id,
            api_key=api_key,
            api_secret=api_secret,
            password=password,
            testnet=testnet,
        )

    return _live_client


def _get_alert_manager() -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        alerts_cfg = get_alerts_config()
        _alert_manager = AlertManager(
            bot_token=alerts_cfg.get("telegram_bot_token", ""),
            chat_id=alerts_cfg.get("telegram_chat_id", ""),
            enabled=alerts_cfg.get("enabled", False),
        )
    return _alert_manager


# ── Helper ──

def _parse_symbols() -> List[dict]:
    """Build symbol list from config"""
    return [
        {"symbol": s, "base": s.replace("USDT", ""), "quote": "USDT"}
        for s in get_trading_symbols()
    ]


def _generate_sample_klines(limit: int = 500):
    """Generate sample OHLCV data for demo/testing"""
    rng = np.random.default_rng(42)
    base = 50000
    data = []
    now = datetime.now()

    for i in range(limit):
        t = now - timedelta(hours=limit - i)
        change = rng.standard_normal() * 200
        base += change
        open_p = base
        close_p = base + rng.standard_normal() * 150
        high_p = max(open_p, close_p) + abs(rng.standard_normal() * 100)
        low_p = min(open_p, close_p) - abs(rng.standard_normal() * 100)
        volume = abs(rng.standard_normal() * 100 + 500)

        data.append({
            'timestamp': int(t.timestamp() * 1000),
            'open': round(open_p, 2),
            'high': round(high_p, 2),
            'low': round(low_p, 2),
            'close': round(close_p, 2),
            'volume': round(volume, 2),
        })

    return data


# ============================================================
# Market Data Endpoints
# ============================================================

@router.get("/market/ticker")
async def get_ticker(symbol: str = Query(default=None)):
    """Get current ticker for a symbol"""
    if symbol is None:
        symbol = get_trading_config().get("default_symbol", "BTCUSDT")
    c = _get_collector()
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


@router.get("/market/klines")
async def get_klines(
    symbol: str = Query(default=None),
    interval: str = "1h",
    limit: int = 500,
    fetch: bool = False,
):
    """Get OHLCV kline data"""
    if symbol is None:
        symbol = get_trading_config().get("default_symbol", "BTCUSDT")

    store = _get_store()
    if fetch:
        c = _get_collector()
        df = c.fetch_and_store(symbol, interval, limit=limit)
    else:
        df = store.load_ohlcv(symbol, interval, limit=limit)

    if df is None or df.empty:
        return _generate_sample_klines(limit)

    # Ensure timestamp column exists (store uses 'open_time')
    if 'open_time' in df.columns:
        df['timestamp'] = pd.to_datetime(df['open_time']).astype('int64') // 10**6
    elif 'timestamp' in df.columns:
        df['timestamp'] = df['timestamp'].astype(int) // 10**6
    else:
        return _generate_sample_klines(limit)

    return df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')


@router.get("/market/symbols")
async def get_symbols():
    """Get available trading symbols"""
    return {"symbols": _parse_symbols()}


# ============================================================
# Exchange Info Endpoint
# ============================================================

@router.get("/exchange/info")
async def get_exchange_info():
    """Return exchange configuration and available symbols."""
    exchange_cfg = get_exchange_config()
    exchange_id = exchange_cfg.get("id", "binance")

    c = _get_collector()
    markets = {}
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
        available_symbols = get_trading_symbols()

    return {
        "exchange_id": exchange_id,
        "testnet": exchange_cfg.get("testnet", True),
        "available_symbols": available_symbols[:50],
        "total_symbols": len(available_symbols),
    }


# ============================================================
# Strategy Endpoints
# ============================================================

@router.get("/strategies")
async def list_strategies():
    """List all registered strategies"""
    return {'strategies': StrategyRegistry.list_strategies()}


@router.get("/strategies/{name}")
async def get_strategy_info(name: str):
    """Get strategy details"""
    strategy_cls = StrategyRegistry.get(name)
    if not strategy_cls:
        raise HTTPException(404, f"Strategy '{name}' not found")
    return {
        'name': name,
        'description': strategy_cls.__doc__ or "",
        'parameters': strategy_cls.get_param_info(),
    }


@router.post("/strategies/reload")
async def reload_strategies():
    """Hot-reload all strategy modules and re-register them via StrategyManager."""
    from strategy.manager import get_strategy_manager
    manager = get_strategy_manager()
    loaded, errors = manager.hot_reload_all()
    strategies = StrategyRegistry.list_strategies()
    return {
        "success": len(errors) == 0,
        "reloaded": [s['name'] for s in strategies],
        "count": len(strategies),
        "errors": errors,
    }


# ============================================================
# Backtest Endpoints
# ============================================================

@router.post("/backtest/compare")
async def run_backtest_compare(request: BacktestRequest):
    """Run all strategies on the same data and return comparison."""
    bt_cfg = get_backtest_config()
    trading_cfg = get_trading_config()
    risk_cfg = get_risk_config()

    symbol = request.symbol
    interval = request.interval

    store = _get_store()
    fetch_limit = 50000 if (request.date_start or request.date_end) else 5000
    df = store.load_ohlcv(symbol, interval, limit=fetch_limit)

    if request.date_start:
        df = df[df.index >= pd.Timestamp(request.date_start)]
    if request.date_end:
        df = df[df.index <= pd.Timestamp(request.date_end) + pd.Timedelta(days=1)]

    if df is None or df.empty:
        sample = _generate_sample_klines(1000)
        df = pd.DataFrame(sample)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

    engine = BacktestEngine(
        initial_capital=request.initial_capital or bt_cfg.get('initial_capital', 10000),
        commission=bt_cfg.get('commission', 0.0004),
        slippage=bt_cfg.get('slippage', 0.0001),
        position_pct=risk_cfg.get('max_position_pct', 0.3),
        default_leverage=trading_cfg.get('default_leverage', 3),
    )

    results = []
    for s in StrategyRegistry.list_strategies():
        try:
            cls = StrategyRegistry.get(s['name'])
            strat = cls()
            strat.params['leverage'] = trading_cfg.get('default_leverage', 3)
            result = engine.run(strat, df, symbol)
            results.append({
                'name': s['name'],
                'trades': result['metrics']['total_trades'],
                'win_rate': result['metrics']['win_rate'],
                'total_return': result['metrics']['total_return'],
                'sharpe_ratio': result['metrics']['sharpe_ratio'],
                'max_drawdown': result['metrics']['max_drawdown'],
                'profit_factor': result['metrics']['profit_factor'],
            })
        except Exception as e:
            results.append({'name': s['name'], 'error': str(e)[:80]})

    return {
        'symbol': symbol, 'interval': interval,
        'candles': len(df),
        'date_start': str(df.index[0].date()), 'date_end': str(df.index[-1].date()),
        'results': results,
    }


@router.post("/backtest/optimize")
async def run_backtest_optimize(request: OptimizeRequest):
    """Run parameter optimization: tries all combinations in param_grid."""
    param_grid = request.param_grid
    if not param_grid:
        raise HTTPException(400, "param_grid is required")

    # Generate all combinations
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(itertools.product(*values))

    if len(combinations) > 50:
        raise HTTPException(400, f"Too many combinations: {len(combinations)} (max 50)")

    strategy_name = request.strategy
    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        raise HTTPException(400, f"Unknown strategy: {strategy_name}")

    symbol = request.symbol
    interval = request.interval
    initial_capital = request.initial_capital or get_backtest_config().get('initial_capital', 10000)
    trading_cfg = get_trading_config()

    # Load data once
    store = _get_store()
    fetch_limit = 50000 if (request.date_start or request.date_end) else 1000
    df = store.load_ohlcv(symbol, interval, limit=fetch_limit)

    if request.date_start:
        df = df[df.index >= pd.Timestamp(request.date_start)]
    if request.date_end:
        df = df[df.index <= pd.Timestamp(request.date_end) + pd.Timedelta(days=1)]

    if df is None or df.empty:
        sample = _generate_sample_klines(1000)
        df = pd.DataFrame(sample)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

    risk_cfg = get_risk_config()
    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission=get_backtest_config().get('commission', 0.0004),
        slippage=get_backtest_config().get('slippage', 0.0001),
        position_pct=risk_cfg.get('max_position_pct', 0.3),
        default_leverage=trading_cfg.get('default_leverage', 3),
    )

    results = []
    for combo in combinations:
        params = dict(zip(keys, combo))
        if 'leverage' not in params:
            params['leverage'] = trading_cfg.get('default_leverage', 3)
        try:
            strategy = strategy_cls(params)
            result = engine.run(strategy, df, symbol)
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

    # Sort by sharpe_ratio descending, valid results first
    valid = [r for r in results if 'error' not in r]
    errors = [r for r in results if 'error' in r]
    valid.sort(key=lambda r: r['sharpe_ratio'] or -999, reverse=True)
    top10 = valid[:10]

    return {
        'strategy': strategy_name,
        'symbol': symbol,
        'interval': interval,
        'total_combinations': len(combinations),
        'best_params': top10[0]['params'] if top10 else None,
        'best_sharpe': top10[0]['sharpe_ratio'] if top10 else None,
        'top10': top10,
        'errors': errors,
    }


@router.post("/backtest")
async def run_backtest(request: BacktestRequest):
    """Run a backtest"""
    bt_cfg = get_backtest_config()
    trading_cfg = get_trading_config()

    strategy_name = request.strategy
    symbol = request.symbol
    interval = request.interval
    initial_capital = request.initial_capital or bt_cfg.get('initial_capital', 10000)
    params = request.params
    days = request.days

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        raise HTTPException(400, f"Unknown strategy: {strategy_name}")

    # Get data — load more if date range specified
    store = _get_store()
    fetch_limit = 50000 if (request.date_start or request.date_end) else 1000
    df = store.load_ohlcv(symbol, interval, limit=fetch_limit)

    if df.empty:
        c = _get_collector()
        df = c.fetch_history(symbol, interval, days=max(days, 730))
    if df is None:
        df = pd.DataFrame()

    # Filter by date range if specified
    if request.date_start or request.date_end:
        if request.date_start:
            df = df[df.index >= pd.Timestamp(request.date_start)]
        if request.date_end:
            df = df[df.index <= pd.Timestamp(request.date_end) + pd.Timedelta(days=1)]
    elif df is not None and not df.empty and len(df) > 1000:
        # Default: use most recent N candles based on days param
        df = df.iloc[-min(len(df), days * 24 if interval == '1h' else days * 6 if interval == '4h' else days):]

    if df is None or df.empty:
        sample = _generate_sample_klines(1000)
        df = pd.DataFrame(sample)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

    # Record actual date range used
    actual_start = str(df.index[0].date())
    actual_end = str(df.index[-1].date())

    # Ensure leverage is available
    if 'leverage' not in params:
        params['leverage'] = trading_cfg.get('default_leverage', 3)

    # Run backtest with config-driven commission, slippage, and position sizing
    risk_cfg = get_risk_config()
    engine = BacktestEngine(
        initial_capital=initial_capital,
        commission=bt_cfg.get('commission', 0.0004),
        slippage=bt_cfg.get('slippage', 0.0001),
        position_pct=risk_cfg.get('max_position_pct', 0.3),
        default_leverage=trading_cfg.get('default_leverage', 3),
    )
    strategy = strategy_cls(params)
    result = engine.run(strategy, df, symbol)

    # Format equity curve for chart
    equity = result['equity_curve'].reset_index()
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
        'strategy': strategy_name,
        'symbol': symbol,
        'interval': interval,
        'params': params,
        'metrics': result['metrics'],
        'equity_curve': equity.to_dict('records'),
        'trades': trades_list,
        'initial_capital': initial_capital,
        'final_capital': result['final_capital'],
        'date_start': actual_start,
        'date_end': actual_end,
        'candles': len(df),
    }


# ============================================================
# Backtest Export Endpoints
# ============================================================

def _run_backtest_for_export(request: BacktestRequest):
    """Shared backtest runner for export endpoints."""
    bt_cfg = get_backtest_config()
    trading_cfg = get_trading_config()
    risk_cfg = get_risk_config()

    strategy_name = request.strategy
    symbol = request.symbol
    interval = request.interval
    initial_capital = request.initial_capital or bt_cfg.get('initial_capital', 10000)
    params = request.params

    strategy_cls = StrategyRegistry.get(strategy_name)
    if not strategy_cls:
        raise HTTPException(400, f"Unknown strategy: {strategy_name}")

    store = _get_store()
    fetch_limit = 50000 if (request.date_start or request.date_end) else 1000
    df = store.load_ohlcv(symbol, interval, limit=fetch_limit)

    if df is None or df.empty:
        c = _get_collector()
        df = c.fetch_history(symbol, interval, days=max(request.days, 730))
    if df is None:
        df = pd.DataFrame()

    if request.date_start or request.date_end:
        if request.date_start:
            df = df[df.index >= pd.Timestamp(request.date_start)]
        if request.date_end:
            df = df[df.index <= pd.Timestamp(request.date_end) + pd.Timedelta(days=1)]
    elif df is not None and not df.empty and len(df) > 1000:
        df = df.iloc[-min(len(df), request.days * 24 if interval == '1h' else request.days * 6 if interval == '4h' else request.days):]

    if df is None or df.empty:
        sample = _generate_sample_klines(1000)
        df = pd.DataFrame(sample)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

    actual_start = str(df.index[0].date())
    actual_end = str(df.index[-1].date())

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
    result['date_start'] = actual_start
    result['date_end'] = actual_end
    result['params'] = params
    result['interval'] = interval
    return result


@router.post("/backtest/export")
async def export_backtest_csv(request: BacktestRequest):
    """Run backtest and return trades as a CSV download."""
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


@router.post("/backtest/export/pdf")
async def export_backtest_pdf(request: BacktestRequest):
    """Run backtest and return a PDF report."""
    result = _run_backtest_for_export(request)
    m = result['metrics']

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


# ============================================================
# Trading Endpoints
# ============================================================

@router.get("/account")
async def get_account():
    """Get account summary"""
    return _get_simulator().get_account_summary()


@router.post("/trade/open")
async def open_trade(request: TradeRequest):
    """Open a new trade"""
    symbol = request.symbol
    side = request.side
    leverage = request.leverage

    c = _get_collector()
    price = c.get_current_price(symbol)
    if not price:
        raise HTTPException(503, f"Cannot get price for {symbol}")

    sim = _get_simulator()
    order = sim.open_position(symbol, side, price, leverage)
    if not order:
        raise HTTPException(400, "Trade rejected by risk manager")

    # Fire trade alert
    _get_alert_manager().send_trade_alert(symbol, side, price)

    # Broadcast account update via WebSocket
    await ws_manager.broadcast_account(sim.get_account_summary())
    return {'success': True, 'order': order}


@router.post("/trade/close")
async def close_trade(request: CloseTradeRequest):
    """Close a position"""
    symbol = request.symbol

    c = _get_collector()
    price = c.get_current_price(symbol)
    if not price:
        raise HTTPException(503, f"Cannot get price for {symbol}")

    sim = _get_simulator()
    order = sim.close_position(symbol, price, "手动平仓")
    if not order:
        raise HTTPException(404, f"No position for {symbol}")

    # Fire trade alert with PnL
    pnl = order.get('pnl', 0) if isinstance(order, dict) else 0
    side = order.get('side', '') if isinstance(order, dict) else ''
    _get_alert_manager().send_trade_alert(symbol, side, price, pnl)

    # Broadcast account update via WebSocket
    await ws_manager.broadcast_account(sim.get_account_summary())
    return {'success': True, 'order': order}


@router.get("/trade/history")
async def get_trade_history(limit: int = 50):
    """Get trade history"""
    sim = _get_simulator()
    history = [o for o in sim.order_history if o['side'] == 'CLOSE']
    return {
        'trades': history[-limit:],
        'total': len(history),
    }


# ============================================================
# Risk Management Endpoints
# ============================================================

@router.get("/risk/summary")
async def get_risk_summary():
    """Get risk management summary"""
    return _get_simulator().risk_manager.get_risk_summary()


@router.post("/risk/limits")
async def update_risk_limits(limits: RiskLimitsRequest):
    """Update risk limits"""
    rm = _get_simulator().risk_manager
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


# ============================================================
# Mode Endpoints
# ============================================================

@router.get("/mode")
async def get_current_mode():
    """Return current trading mode."""
    return {'mode': get_mode()}


@router.post("/mode")
async def set_mode(request: ModeRequest):
    """Switch trading mode between paper and live."""
    if request.mode == "live":
        binance_cfg = get_binance_config()
        api_key = binance_cfg.get("api_key", "")
        api_secret = binance_cfg.get("api_secret", "")
        if not api_key or not api_secret:
            raise HTTPException(400, "Live mode requires Binance API keys configured in config.yaml")
        # Verify connection
        client = _get_live_client()
        if client is None:
            raise HTTPException(400, "Cannot initialize Binance client — check API keys")
        try:
            client.is_connected()
        except Exception as e:
            raise HTTPException(400, f"Binance connection test failed: {e}")

    # Update config in memory (persisted to config.yaml)
    config = get_config()
    config["mode"] = request.mode
    return {'success': True, 'mode': request.mode}


# ============================================================
# Alert Endpoints (basic Telegram alerts — separate from alert_routes.py custom alerts)
# ============================================================

@router.post("/alerts/test")
async def test_alert(request: AlertTestRequest):
    """Send a test message via Telegram to verify integration."""
    alerts_cfg = get_alerts_config()
    am = _get_alert_manager()
    success = am.send_telegram(
        message=request.message,
        bot_token=alerts_cfg.get("telegram_bot_token", ""),
        chat_id=alerts_cfg.get("telegram_chat_id", ""),
    )
    if not success:
        raise HTTPException(400, "Failed to send Telegram alert — check bot token and chat ID")
    return {'success': True, 'message': request.message}


@router.post("/alerts/config")
async def update_alert_config(request: AlertConfigRequest):
    """Update alert configuration at runtime."""
    global _alert_manager
    am = _get_alert_manager()
    am.configure(request.bot_token, request.chat_id, request.enabled)

    # Persist to in-memory config
    config = get_config()
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


# ============================================================
# System Endpoints
# ============================================================

@router.get("/system/status")
async def get_system_status():
    """Get system status"""
    db_path = get_db_path()
    traders = trading_scheduler.list_traders()
    return {
        'mode': get_mode(),
        'uptime': 'running',
        'active_bots': len(active_bots),
        'live_traders': len(traders),
        'trader_list': traders,
        'database_size': os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        'ws_market': ws_manager.market_connections,
        'ws_account': ws_manager.account_connections,
    }


# ============================================================
# Quick-Start (一键开箱) Endpoint
# ============================================================

@router.post("/quick-start")
async def quick_start():
    """一键开箱：自动配置最优策略并启动模拟盘"""
    try:
        from execution.live_trader import LivePaperTrader
        from execution.simulator import PaperTradingSimulator
        from risk.manager import RiskManager, RiskLimits
        from strategy.rsi_mean_reversion import RSIMeanReversionStrategy
        from data.store import DataStore
        from config import get_db_path

        trading = get_trading_config()
        risk_cfg = get_risk_config()

        symbol = trading.get("default_symbol", "BTCUSDT")
        interval = "1h"

        # 使用RSI均值回归（回测表现最好的策略）
        strategy = RSIMeanReversionStrategy(
            rsi_period=14,
            oversold=30,
            overbought=70,
            use_divergence=True,
        )

        # 创建模拟器
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
            initial_capital=get_backtest_config().get("initial_capital", 10000),
            risk_limits=limits,
        )

        # 启动实盘模拟
        store = DataStore(get_db_path())
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
        raise HTTPException(500, f"模块导入失败: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"一键启动失败: {str(e)}")


# ============================================================
# Live Status Endpoint (for quick-start area check)
# ============================================================

@router.get("/live/status")
async def get_live_status():
    """Get status of all live trading bots."""
    bots_status = {}
    for bot_id, trader in active_bots.items():
        try:
            bots_status[bot_id] = trader.status if hasattr(trader, 'status') else {"running": trader.is_running if hasattr(trader, 'is_running') else False}
        except Exception:
            bots_status[bot_id] = {"running": False}

    return {
        "bots": bots_status,
        "count": len(bots_status),
        "running_count": sum(1 for b in bots_status.values() if b.get("running", False)),
    }


# ============================================================
# Live Stop Endpoint
# ============================================================

@router.post("/live/stop")
async def stop_live_trader(request: dict):
    """Stop a live paper trading bot."""
    bot_id = request.get("name") or request.get("bot_id", "")
    if not bot_id:
        raise HTTPException(400, "bot_id or name is required")

    trader = active_bots.get(bot_id)
    if not trader:
        raise HTTPException(404, f"Bot '{bot_id}' not found")

    try:
        if hasattr(trader, 'stop'):
            await trader.stop()
        del active_bots[bot_id]
        return {"success": True, "bot_id": bot_id}
    except Exception as e:
        raise HTTPException(500, f"停止失败: {str(e)}")


# ============================================================
# Live Trading Endpoints (via TradingScheduler)
# ============================================================

@router.post("/live/start")
async def start_live_trader(request: StartTraderRequest):
    """Start a live paper trading bot via the scheduler."""
    await trading_scheduler.start_trader(
        name=request.name,
        strategy_name=request.strategy,
        symbol=request.symbol,
        leverage=request.leverage,
        interval_seconds=request.interval_seconds,
    )
    return {"success": True, "name": request.name, "strategy": request.strategy}


@router.get("/live/reports")
async def get_live_reports():
    """Get latest trading reports from all bots."""
    reports = trading_scheduler.get_all_reports()
    # Also include text versions
    result = {}
    for name, report in reports.items():
        reporter = trading_scheduler.get_reporter(name)
        text = reporter.format_text_report(report) if reporter else ""
        result[name] = {'json': report, 'text': text}
    return {'reports': result, 'count': len(result)}


@router.get("/live/report/{name}")
async def get_live_report(name: str):
    """Get detailed report for a specific trader."""
    reporter = trading_scheduler.get_reporter(name)
    if not reporter:
        raise HTTPException(404, f"Trader '{name}' not found")
    report = reporter.get_latest_report()
    if not report:
        return {'name': name, 'report': None, 'message': 'No report generated yet'}
    text = reporter.format_text_report(report)
    return {'name': name, 'report': report, 'text': text}


# ============================================================
# WebSocket Endpoints
# ============================================================

@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    """Real-time market data stream (ticker updates)."""
    await ws_manager.connect(websocket, "market")
    try:
        while True:
            # Keep connection alive, client sends pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_manager.disconnect(websocket, "market")


@router.websocket("/ws/account")
async def ws_account(websocket: WebSocket):
    """Real-time account updates (positions, equity changes)."""
    await ws_manager.connect(websocket, "account")
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
        ws_manager.disconnect(websocket, "account")


# ============================================================
# Sub-router registration (all under /api prefix)
# ============================================================

from web.strategy_admin_routes import router as strategy_admin_router
from web.backup_routes import router as backup_router
from web.data_admin_routes import router as data_router
from web.alert_routes import router as alert_custom_router
from web.note_routes import router as note_router
from web.recommend_routes import router as recommend_router
from web.exchange_routes import router as exchange_router

router.include_router(strategy_admin_router)
router.include_router(backup_router)
router.include_router(data_router)
router.include_router(alert_custom_router)
router.include_router(note_router)
router.include_router(recommend_router)
router.include_router(exchange_router)
