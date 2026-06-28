"""
交易所管理 API — 支持币安/欧易切换、查看状态、测试连接
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/exchange", tags=["exchange"])


# ── Lazy imports ──

def _import_config():
    try:
        from config import (
            get_exchange_id, get_exchange_config, get_binance_config,
            get_okx_config, get_config
        )
        return {
            'get_exchange_id': get_exchange_id,
            'get_exchange_config': get_exchange_config,
            'get_binance_config': get_binance_config,
            'get_okx_config': get_okx_config,
            'get_config': get_config,
        }
    except ImportError as e:
        logger.warning(f"Config module not available: {e}")
        return None


def _import_client():
    try:
        from execution.client import MultiExchangeClient, SUPPORTED_EXCHANGES
        return MultiExchangeClient, SUPPORTED_EXCHANGES
    except ImportError as e:
        logger.warning(f"MultiExchangeClient not available: {e}")
        return None, []


# ── Models ──

class ExchangeSwitchRequest(BaseModel):
    exchange_id: str  # 'binance' or 'okx'


class ExchangeKeyRequest(BaseModel):
    exchange_id: str
    api_key: str
    api_secret: str
    password: Optional[str] = None  # OKX requires this
    testnet: bool = True


# ── 交易所状态 ──

@router.get("/status")
async def exchange_status():
    """查看当前交易所状态和配置"""
    try:
        cfg = _import_config()
        if cfg is None:
            return {"error": "配置模块不可用", "detail": "Config module not available"}

        ex_id = cfg['get_exchange_id']()
        bin_cfg = cfg['get_binance_config']()
        okx_cfg = cfg['get_okx_config']()

        _, SUPPORTED_EXCHANGES = _import_client()

        return {
            "active": ex_id,
            "available": SUPPORTED_EXCHANGES if SUPPORTED_EXCHANGES else ["binance", "okx"],
            "binance": {
                "configured": bool(bin_cfg.get("api_key")),
                "testnet": bin_cfg.get("testnet", True),
            },
            "okx": {
                "configured": bool(okx_cfg.get("api_key")),
                "testnet": okx_cfg.get("testnet", True),
            },
        }
    except Exception as e:
        return {"error": "无法获取交易所状态", "detail": str(e)}


# ── 交易所列表 ──

@router.get("/list")
async def exchange_list():
    """列出所有支持的交易所"""
    return {
        "exchanges": [
            {
                "id": "binance",
                "name": "币安 Binance",
                "type": "永续合约 USDT-M",
                "testnet_url": "https://testnet.binancefuture.com/",
                "api_doc": "https://binance-docs.github.io/apidocs/futures/cn/",
            },
            {
                "id": "okx",
                "name": "欧易 OKX",
                "type": "永续合约 USDT",
                "testnet_url": "https://www.okx.com/zh-hans/help/how-can-i-access-the-trading-demo",
                "api_doc": "https://www.okx.com/docs-v5/zh/",
            },
        ]
    }


# ── 测试连接 ──

@router.post("/test")
async def test_connection(req: ExchangeKeyRequest):
    """测试交易所 API 连接是否正常"""
    try:
        MultiExchangeClient, _ = _import_client()
        if MultiExchangeClient is None:
            return {
                "error": "交易所客户端不可用",
                "detail": "ccxt/MultiExchangeClient not available in this environment"
            }

        client = MultiExchangeClient(
            exchange_id=req.exchange_id,
            api_key=req.api_key,
            api_secret=req.api_secret,
            password=req.password or "",
            testnet=req.testnet,
        )

        if not client.is_connected():
            return {"error": "连接失败", "detail": "请检查 API Key 和网络"}

        # 尝试获取余额验证权限
        balance = client.get_balance("USDT")
        ticker = client.get_ticker("BTCUSDT")

        return {
            "success": True,
            "exchange": req.exchange_id,
            "testnet": req.testnet,
            "balance_usdt": balance,
            "btc_price": ticker.get("last") if ticker else None,
            "message": f"{req.exchange_id} 连接成功",
        }
    except Exception as e:
        return {"error": "连接测试失败", "detail": str(e)}


# ── 切换交易所 ──

@router.post("/switch")
async def switch_exchange(req: ExchangeSwitchRequest):
    """运行时切换活跃交易所（需要重启调度器生效）"""
    try:
        _, SUPPORTED_EXCHANGES = _import_client()
        supported = SUPPORTED_EXCHANGES if SUPPORTED_EXCHANGES else ["binance", "okx"]

        if req.exchange_id not in supported:
            return {
                "error": "不支持的交易所",
                "detail": f"不支持: {req.exchange_id}。支持: {supported}"
            }

        cfg = _import_config()
        if cfg is None:
            return {"error": "配置模块不可用", "detail": "Config module not available"}

        config = cfg['get_config']()
        if "exchange" not in config:
            config["exchange"] = {}
        config["exchange"]["id"] = req.exchange_id

        return {
            "success": True,
            "active": req.exchange_id,
            "message": f"已切换到 {req.exchange_id}，请重启交易机器人使配置生效",
        }
    except Exception as e:
        return {"error": "切换交易所失败", "detail": str(e)}
