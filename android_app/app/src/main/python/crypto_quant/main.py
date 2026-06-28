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
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from config import get_mode, get_web_config
from web.routes import router as api_router
from web.evolution_routes import router as evolution_router

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="加密货币量化交易系统",
    description="基于 Binance 永续合约的 Web 量化交易系统，支持策略回测、模拟交易、风险管理",
    version="1.0.0",
)

# CORS — allow_credentials cannot be combined with wildcard origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(api_router)
app.include_router(evolution_router)

# Static files
static_dir = Path(__file__).parent / "web" / "static"
try:
    static_dir.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    pass  # Android may not allow mkdir in app directory
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup_event():
    """Start background trading scheduler on server startup."""
    from execution.scheduler import scheduler
    await scheduler.start()
    logger.info("Trading scheduler started")


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background trading scheduler on server shutdown."""
    from execution.scheduler import scheduler
    await scheduler.stop()
    logger.info("Trading scheduler stopped")


@app.get("/")
async def root():
    """Serve main page"""
    from fastapi.responses import FileResponse
    return FileResponse(str(static_dir / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "mode": get_mode()}


if __name__ == "__main__":
    web = get_web_config()
    logger.info(f"Starting server on {web.get('host', '0.0.0.0')}:{web.get('port', 8000)}")
    logger.info(f"Mode: {get_mode()}")
    uvicorn.run(
        "main:app",
        host=web.get('host', '0.0.0.0'),
        port=web.get('port', 8000),
        reload=(get_mode() == "paper"),
    )
