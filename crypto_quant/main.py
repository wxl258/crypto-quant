"""
Cryptocurrency Quantitative Trading System
基于 Binance 永续合约的 Web 量化交易系统
"""
import sys
import os
# Add project root to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import logging
import uvicorn
import argparse
import multiprocessing
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from config import get_mode, get_web_config
from version import __version__
from web.routes import router as api_router
from web.evolution_routes import router as evolution_router
from logging_config import setup_logging, set_trace_id

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="加密货币量化交易系统",
    description="基于 Binance 永续合约的 Web 量化交易系统，支持策略回测、模拟交易、风险管理",
    version=__version__,
)

# CORS — allow_credentials cannot be combined with wildcard origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """从请求头提取 X-Request-ID 并设置到日志上下文。"""
    trace_id = request.headers.get("X-Request-ID", "-")
    set_trace_id(trace_id)
    response = await call_next(request)
    return response

# API Routes
app.include_router(api_router)
app.include_router(evolution_router)

# Static files
static_dir = Path(__file__).parent / "web" / "static"
try:
    static_dir.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError) as e:
    logger.debug(f"Cannot create static directory: {e}")
    pass  # Android may not allow mkdir in app directory

# Only mount static files if directory exists
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    logger.warning(f"Static directory not found: {static_dir}")


@app.on_event("startup")
async def startup_event():
    """Start background trading scheduler on server startup."""
    try:
        from execution.scheduler import scheduler
        await scheduler.start()
        logger.info("Trading scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler start failed (non-critical): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """优雅关闭 — 停止调度器、关闭 WebSocket、持久化持仓、关闭数据库连接。"""
    import asyncio
    from config import get_db_path

    async def _shutdown():
        # 1. 停止 trading scheduler
        try:
            from execution.scheduler import scheduler
            await scheduler.stop()
            logger.info("Trading scheduler stopped")
        except Exception as e:
            logger.warning(f"Scheduler stop failed (non-critical): {e}")

        # 2. 关闭所有 WebSocket 连接
        try:
            from web.websocket import ws_manager
            for channel in list(ws_manager._connections.keys()):
                for ws in list(ws_manager._connections[channel]):
                    try:
                        await ws.close(code=1001, reason="Server shutting down")
                    except Exception:
                        pass
                ws_manager._connections[channel].clear()
            logger.info("WebSocket connections closed")
        except Exception as e:
            logger.warning(f"WebSocket close failed (non-critical): {e}")

        # 3. 持久化当前持仓状态
        try:
            from data.store import DataStore
            store = DataStore(get_db_path())
            positions = store.load_open_positions()
            if positions:
                logger.info(f"Persisted {len(positions)} open positions")
            else:
                logger.info("No open positions to persist")
        except Exception as e:
            logger.warning(f"Position persistence failed (non-critical): {e}")

        # 4. 关闭数据库连接
        try:
            store = DataStore(get_db_path())
            store.close()
            logger.info("Database connection closed")
        except Exception as e:
            logger.warning(f"Database close failed (non-critical): {e}")

    try:
        await asyncio.wait_for(_shutdown(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("Shutdown timed out after 10 seconds — forcing exit")
    except Exception as e:
        logger.warning(f"Shutdown event failed (non-critical): {e}")


@app.get("/")
async def root():
    """Serve main page"""
    from fastapi.responses import FileResponse, HTMLResponse
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    # Fallback: return simple HTML if index.html not found
    return HTMLResponse("""
    <!DOCTYPE html><html><head><meta charset="utf-8">
    <title>CryptoQuant</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:sans-serif;background:#1a1a2e;color:#eee;display:flex;
    justify-content:center;align-items:center;height:100vh;margin:0}
    .box{text-align:center}h1{color:#0af}button{padding:12px 24px;background:#0af;
    border:none;border-radius:8px;color:#000;font-size:16px;cursor:pointer}</style></head>
    <body><div class="box"><h1>🚀 CryptoQuant</h1>
    <p>量化交易系统已就绪</p><p>API 状态正常，请检查网络连接</p>
    <button onclick="location.reload()">刷新页面</button></div></body></html>""")


@app.get("/health")
async def health():
    return {"status": "ok", "mode": get_mode()}


@app.get("/health/deep")
async def deep_health():
    """深度健康检查 — 检查数据库、交易所、trading bot 状态和内存使用率。"""
    import psutil
    from config import get_db_path, get_binance_config

    checks: dict[str, str] = {}

    # 数据库连接检查
    try:
        from data.store import DataStore
        store = DataStore(get_db_path())
        store.load_ohlcv("BTCUSDT", "1h", limit=1)
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # 交易所连接检查（仅当配置了 API Key 时）
    try:
        binance_cfg = get_binance_config()
        api_key = binance_cfg.get("api_key", "") if binance_cfg else ""
        if api_key and api_key != "YOUR_BINANCE_API_KEY":
            import ccxt
            exchange = ccxt.binance({
                "apiKey": api_key,
                "secret": binance_cfg.get("api_secret", ""),
                "enableRateLimit": True,
            })
            exchange.fetch_time()
            checks["exchange"] = "ok"
        else:
            checks["exchange"] = "skipped (no API key configured)"
    except Exception as e:
        checks["exchange"] = f"error: {e}"

    # 活跃 trading bot 状态检查
    try:
        from execution.scheduler import scheduler
        traders = scheduler.list_traders()
        checks["trading_bot"] = f"ok ({len(traders)} traders, scheduler {'running' if scheduler.is_running else 'stopped'})"
    except Exception as e:
        checks["trading_bot"] = f"error: {e}"

    # 内存使用率
    try:
        mem = psutil.virtual_memory()
        checks["memory"] = f"{mem.percent:.1f}% used ({mem.available // (1024 * 1024)} MB free)"
    except Exception as e:
        checks["memory"] = f"error: {e}"

    all_ok = all(
        v == "ok" or v.startswith("skipped")
        for v in checks.values()
    )
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CryptoQuant 量化交易系统")
    parser.add_argument("--workers", type=int, default=None, help="Worker 数量（默认使用 CPU 核心数）")
    args = parser.parse_args()

    web = get_web_config()
    host = web.get('host', '0.0.0.0')
    port = web.get('port', 8000)
    workers = args.workers or int(os.environ.get('WORKERS', multiprocessing.cpu_count()))
    is_dev = get_mode() == "paper"

    logger.info(f"Starting server on {host}:{port}")
    logger.info(f"Mode: {get_mode()}")
    logger.info(f"Workers: {workers}")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        workers=workers if not is_dev else 1,
        reload=is_dev,
    )
