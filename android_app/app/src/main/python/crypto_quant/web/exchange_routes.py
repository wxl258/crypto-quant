"""
交易所管理 API — 支持币安/欧易切换、查看状态、测试连接
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from config import (
    get_exchange_id, get_exchange_config, get_binance_config, get_okx_config,
    get_config
)
from execution.client import MultiExchangeClient, SUPPORTED_EXCHANGES

router = APIRouter(prefix="/exchange", tags=["exchange"])


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
    ex_id = get_exchange_id()
    bin_cfg = get_binance_config()
    okx_cfg = get_okx_config()

    return {
        "active": ex_id,
        "available": SUPPORTED_EXCHANGES,
        "binance": {
            "configured": bool(bin_cfg.get("api_key")),
            "testnet": bin_cfg.get("testnet", True),
        },
        "okx": {
            "configured": bool(okx_cfg.get("api_key")),
            "testnet": okx_cfg.get("testnet", True),
        },
    }


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
        client = MultiExchangeClient(
            exchange_id=req.exchange_id,
            api_key=req.api_key,
            api_secret=req.api_secret,
            password=req.password or "",
            testnet=req.testnet,
        )

        if not client.is_connected():
            raise HTTPException(400, "连接失败，请检查 API Key 和网络")

        # 尝试获取余额验证权限
        balance = client.get_balance("USDT")
        ticker = client.get_ticker("BTCUSDT")

        return {
            "success": True,
            "exchange": req.exchange_id,
            "testnet": req.testnet,
            "balance_usdt": balance,
            "btc_price": ticker.get("last"),
            "message": f"{req.exchange_id} 连接成功",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"连接测试失败: {str(e)}")


# ── 切换交易所 ──

@router.post("/switch")
async def switch_exchange(req: ExchangeSwitchRequest):
    """运行时切换活跃交易所（需要重启调度器生效）"""
    if req.exchange_id not in SUPPORTED_EXCHANGES:
        raise HTTPException(400,
            f"不支持的交易所: {req.exchange_id}。支持: {SUPPORTED_EXCHANGES}")

    # 修改内存中的配置
    config = get_config()
    if "exchange" not in config:
        config["exchange"] = {}
    config["exchange"]["id"] = req.exchange_id

    return {
        "success": True,
        "active": req.exchange_id,
        "message": f"已切换到 {req.exchange_id}，请重启交易机器人使配置生效",
    }
