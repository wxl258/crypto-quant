"""
自定义告警 API
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter(prefix="/alerts/custom", tags=["custom-alerts"])


class AlertCreate(BaseModel):
    type: str  # price / pnl / time
    symbol: Optional[str] = ""
    condition: str  # above / below
    value: float
    message: str = ""
    enabled: bool = True


class AlertToggle(BaseModel):
    alert_id: str
    enabled: bool


@router.get("/list")
async def list_alerts():
    from execution.alert_engine import get_alert_engine
    engine = get_alert_engine()
    return {"alerts": engine.list_alerts(), "count": len(engine.list_alerts())}


@router.post("/create")
async def create_alert(req: AlertCreate):
    from execution.alert_engine import get_alert_engine
    engine = get_alert_engine()
    alert = engine.add_alert(req.model_dump())
    return {"success": True, "alert": alert}


@router.post("/toggle")
async def toggle_alert(req: AlertToggle):
    from execution.alert_engine import get_alert_engine
    engine = get_alert_engine()
    success = engine.toggle_alert(req.alert_id, req.enabled)
    if not success:
        raise HTTPException(404, "告警不存在")
    return {"success": True}


@router.delete("/{alert_id}")
async def delete_alert(alert_id: str):
    from execution.alert_engine import get_alert_engine
    engine = get_alert_engine()
    success = engine.remove_alert(alert_id)
    if not success:
        raise HTTPException(404, "告警不存在")
    return {"success": True}
