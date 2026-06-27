"""
自定义告警引擎 — 支持用户自定义价格/指标条件告警
"""
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

ALERTS_FILE = Path(__file__).parent.parent / "data" / "custom_alerts.json"


class AlertEngine:
    def __init__(self):
        self._alerts = self._load()

    def _load(self) -> List[Dict]:
        if ALERTS_FILE.exists():
            try:
                return json.loads(ALERTS_FILE.read_text())
            except:
                return []
        return []

    def _save(self):
        ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERTS_FILE.write_text(json.dumps(self._alerts, indent=2))

    def add_alert(self, alert: Dict) -> Dict:
        """添加告警
        alert格式: {
            "type": "price"|"pnl"|"time"|"rsi",
            "symbol": "BTCUSDT",
            "condition": "above"|"below"|"cross",
            "value": 60000,
            "message": "BTC价格突破60000",
            "enabled": true
        }
        """
        alert["id"] = str(int(datetime.now().timestamp() * 1000))
        alert["created_at"] = datetime.now().isoformat()
        alert["triggered"] = False
        alert["triggered_at"] = None
        self._alerts.append(alert)
        self._save()
        return alert

    def remove_alert(self, alert_id: str) -> bool:
        for i, a in enumerate(self._alerts):
            if a.get("id") == alert_id:
                self._alerts.pop(i)
                self._save()
                return True
        return False

    def toggle_alert(self, alert_id: str, enabled: bool) -> bool:
        for a in self._alerts:
            if a.get("id") == alert_id:
                a["enabled"] = enabled
                self._save()
                return True
        return False

    def list_alerts(self) -> List[Dict]:
        return self._alerts

    def check_price_alert(self, symbol: str, price: float) -> List[Dict]:
        """检查价格告警"""
        triggered = []
        for a in self._alerts:
            if not a.get("enabled", True) or a.get("triggered"):
                continue
            if a.get("type") != "price":
                continue
            if a.get("symbol") != symbol:
                continue

            condition = a.get("condition", "above")
            target = a.get("value", 0)

            should_trigger = False
            if condition == "above" and price >= target:
                should_trigger = True
            elif condition == "below" and price <= target:
                should_trigger = True

            if should_trigger:
                a["triggered"] = True
                a["triggered_at"] = datetime.now().isoformat()
                triggered.append(a)

        if triggered:
            self._save()
        return triggered

    def check_pnl_alert(self, daily_pnl: float, daily_pnl_pct: float) -> List[Dict]:
        """检查盈亏告警"""
        triggered = []
        for a in self._alerts:
            if not a.get("enabled", True) or a.get("triggered"):
                continue
            if a.get("type") != "pnl":
                continue

            condition = a.get("condition", "below")
            target = abs(a.get("value", 0))

            should_trigger = False
            if condition == "below" and daily_pnl <= -target:
                should_trigger = True

            if should_trigger:
                a["triggered"] = True
                a["triggered_at"] = datetime.now().isoformat()
                triggered.append(a)

        if triggered:
            self._save()
        return triggered

    def reset_triggered(self):
        """重置所有已触发的告警（每天重置）"""
        for a in self._alerts:
            a["triggered"] = False
            a["triggered_at"] = None
        self._save()


# 全局单例
_alert_engine: Optional[AlertEngine] = None


def get_alert_engine() -> AlertEngine:
    global _alert_engine
    if _alert_engine is None:
        _alert_engine = AlertEngine()
    return _alert_engine
