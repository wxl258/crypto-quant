"""
策略管理 API — 热插拔、外部加载、启用/禁用
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies/admin", tags=["strategy-admin"])

class DownloadRequest(BaseModel):
    url: str
    sha256: Optional[str] = ""

class ToggleRequest(BaseModel):
    name: str

# ── Helper: safe import of strategy manager ──

def _get_strategy_manager():
    """Safely import and return the strategy manager, or None if unavailable."""
    try:
        from strategy.manager import get_strategy_manager
        return get_strategy_manager()
    except ImportError as e:
        logger.warning(f"Strategy manager not available: {e}")
        return None

def _get_strategy_registry():
    """Safely import and return the StrategyRegistry, or None if unavailable."""
    try:
        from strategy import StrategyRegistry
        return StrategyRegistry
    except ImportError as e:
        logger.warning(f"StrategyRegistry not available: {e}")
        return None

# ── 热重载 ──

@router.post("/reload")
async def hot_reload():
    """热重载所有策略（不会中断正在运行的交易）"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    loaded, errors = mgr.hot_reload_all()
    return {
        "success": len(errors) == 0,
        "loaded": loaded,
        "count": len(loaded),
        "errors": errors,
    }

# ── 外部下载 ──

@router.post("/download")
async def download_strategy(req: DownloadRequest):
    """从URL下载并加载策略"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    success, msg = mgr.download_strategy(req.url, req.sha256 or "")
    if not success:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

# ── 启用/禁用 ──

@router.post("/disable")
async def disable_strategy(req: ToggleRequest):
    """禁用策略"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    success, msg = mgr.disable_strategy(req.name)
    if not success:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

@router.post("/enable")
async def enable_strategy(req: ToggleRequest):
    """启用策略"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    success, msg = mgr.enable_strategy(req.name)
    if not success:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

# ── 删除 ──

@router.post("/delete")
async def delete_strategy(req: ToggleRequest):
    """删除自定义策略"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    success, msg = mgr.delete_strategy(req.name)
    if not success:
        raise HTTPException(400, msg)
    return {"success": True, "message": msg}

# ── 查看源码 ──

@router.get("/source/{name}")
async def get_strategy_source(name: str):
    """获取策略源码"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    source = mgr.get_strategy_source(name)
    if source is None:
        raise HTTPException(404, "策略不存在或无法获取源码")
    return {"name": name, "source": source}

# ── 详细信息 ──

@router.get("/info/{name}")
async def get_strategy_detail(name: str):
    """获取策略详细信息（含元数据）"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    info = mgr.get_strategy_info(name)
    if info is None:
        raise HTTPException(404, f"策略 '{name}' 不存在")
    return info

# ── 发现策略 ──

@router.get("/discover")
async def discover_strategies():
    """自动发现所有可用策略"""
    mgr = _get_strategy_manager()
    if mgr is None:
        raise HTTPException(503, "策略管理器不可用")
    names = mgr.discover_strategies()
    return {"strategies": names, "count": len(names)}

# ── 策略模板下载 ──

@router.get("/template")
async def get_strategy_template():
    """获取策略开发模板（帮助用户写自定义策略）"""
    template = '''"""
我的自定义策略 — 在这里写你的交易逻辑
"""
from strategy.base import Strategy, Signal, SignalType
import pandas as pd
import numpy as np


class MyCustomStrategy(Strategy):
    """自定义策略描述"""
    
    @classmethod
    def get_param_info(cls):
        return [
            {"name": "param1", "type": "int", "default": 14, "description": "参数1说明"},
            {"name": "param2", "type": "float", "default": 2.0, "description": "参数2说明"},
        ]
    
    def init(self):
        """初始化：在这里预计算所有需要的指标"""
        # 获取参数
        param1 = self.get_param("param1", 14)
        param2 = self.get_param("param2", 2.0)
        
        # 预计算指标（使用基类提供的方法）
        # self.rsi_series = self.rsi(param1)
        # self.bb_upper, self.bb_mid, self.bb_lower = self.bollinger_bands(param1, param2)
        
        pass
    
    def next(self, i: int):
        """每个K线调用一次，i是当前索引"""
        if i < 50:  # 预热期，跳过
            return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)
        
        # === 在这里写你的交易逻辑 ===
        
        # 示例：简单的均线交叉
        # if self.data['close'][i] > self.sma(20)[i]:
        #     return Signal(
        #         signal_type=SignalType.BUY,
        #         symbol=self.symbol,
        #         price=self.data['close'][i],
        #         reason="价格在20日均线上方"
        #     )
        
        # 默认：不交易
        return Signal(signal_type=SignalType.HOLD, symbol=self.symbol, price=0)
'''
    return {"template": template, "language": "python"}

# ── 批量信息 ──

@router.post("/info/batch")
async def get_strategies_info_batch(request: dict = None):
    """批量获取策略信息"""
    try:
        if request is None:
            request = {}
        names = request.get('names', [])
        if not names:
            StrategyRegistry = _get_strategy_registry()
            if StrategyRegistry is None:
                return {"error": "StrategyRegistry不可用"}
            names = [s['name'] for s in StrategyRegistry.list_strategies()]
        
        mgr = _get_strategy_manager()
        results = {}
        for name in names:
            try:
                if mgr is not None and hasattr(mgr, 'get_strategy_info'):
                    info = mgr.get_strategy_info(name)
                    results[name] = info if info else {"name": name, "status": "unavailable"}
                else:
                    results[name] = {"name": name, "status": "unavailable"}
            except Exception:
                results[name] = {"name": name, "status": "error"}
        return {"strategies": results}
    except Exception as e:
        return {"error": str(e)}
