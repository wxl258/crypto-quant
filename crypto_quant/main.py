"""
Cryptocurrency Quantitative Trading System
基于 Binance 永续合约的 Web 量化交易系统
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import logging
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from config import get_mode, get_web_config
from version import __version__
from web.routes import router as api_router
from web.evolution_routes import router as evolution_router

# Setup logging (Android-safe: fallback to basicConfig)
try:
    from logging_config import setup_logging, set_trace_id
    setup_logging()
    _has_logging_config = True
except (ImportError, Exception):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    _has_logging_config = False
    def set_trace_id(_): pass

logger = logging.getLogger(__name__)

app = FastAPI(
    title="加密货币量化交易系统",
    description="基于 Binance 永续合约的 Web 量化交易系统，支持策略回测、模拟交易、风险管理",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _has_logging_config:
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Request-ID", "-")
        set_trace_id(trace_id)
        response = await call_next(request)
        return response

app.include_router(api_router)
app.include_router(evolution_router)

static_dir = Path(__file__).parent / "web" / "static"
try:
    static_dir.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    logger.warning(f"Static directory not found: {static_dir}")


@app.on_event("startup")
async def startup_event():
    try:
        from execution.scheduler import scheduler
        await scheduler.start()
        logger.info("Trading scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler start failed (non-critical): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    try:
        from execution.scheduler import scheduler
        await scheduler.stop()
        logger.info("Trading scheduler stopped")
    except Exception:
        pass


@app.get("/")
async def root():
    from fastapi.responses import FileResponse, HTMLResponse
    index_path = static_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("""
    <!DOCTYPE html><html><head><meta charset="utf-8">
    <title>CryptoQuant</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:sans-serif;background:#1a1a2e;color:#eee;display:flex;
    justify-content:center;align-items:center;height:100vh;margin:0}
    .box{text-align:center}h1{color:#0af}button{padding:12px 24px;background:#0af;
    border:none;border-radius:8px;color:#000;font-size:16px;cursor:pointer}</style></head>
    <body><div class="box"><h1>CryptoQuant</h1>
    <p>v""" + __version__ + """</p><p>API /docs</p></div></body></html>""")


@app.get("/health")
async def health():
    return {"status": "ok", "mode": get_mode(), "version": __version__}


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="CryptoQuant")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    web = get_web_config()
    host = web.get('host', '0.0.0.0')
    port = args.port or web.get('port', 8000)
    workers = args.workers or int(os.environ.get('WORKERS', '1'))

    logger.info(f"v{__version__} on {host}:{port}, mode={get_mode()}")
    uvicorn.run("main:app", host=host, port=port, workers=workers, reload=(get_mode() == "paper"))
