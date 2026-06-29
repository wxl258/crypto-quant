"""
WebSocket Manager - Real-time data streaming for the trading dashboard
"""
import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

MAX_CONNECTIONS = 50
HEARTBEAT_TIMEOUT = 30  # 30 秒无心跳自动断开


class WebSocketManager:
    """Manages WebSocket connections and broadcasts real-time updates."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {
            "market": set(),   # Price/ticker updates
            "account": set(),  # Account/position changes
        }
        self._heartbeat_tasks: Dict[WebSocket, asyncio.Task] = {}

    @property
    def active_connections(self) -> int:
        """返回所有活跃连接的总数。"""
        return sum(len(conns) for conns in self._connections.values())

    async def connect(self, websocket: WebSocket, channel: str = "market"):
        if self.active_connections >= MAX_CONNECTIONS:
            await websocket.close(code=1013, reason="Too many connections")
            logger.warning(f"WebSocket 连接被拒绝：已达最大连接数 {MAX_CONNECTIONS}")
            return
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)
        self._start_heartbeat(websocket, channel)
        logger.info(f"WebSocket connected to {channel} (total: {len(self._connections[channel])})")

    def disconnect(self, websocket: WebSocket, channel: str = "market"):
        if channel in self._connections:
            self._connections[channel].discard(websocket)
        self._stop_heartbeat(websocket)
        logger.info(f"WebSocket disconnected from {channel}")

    def _start_heartbeat(self, websocket: WebSocket, channel: str) -> None:
        """启动心跳检测任务。"""
        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_TIMEOUT)
                    try:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        break
            except asyncio.CancelledError:
                pass
            finally:
                self.disconnect(websocket, channel)
        self._heartbeat_tasks[websocket] = asyncio.create_task(heartbeat())

    def _stop_heartbeat(self, websocket: WebSocket) -> None:
        """停止心跳检测任务。"""
        task = self._heartbeat_tasks.pop(websocket, None)
        if task and not task.done():
            task.cancel()

    async def broadcast(self, channel: str, data: dict):
        """Send data to all clients subscribed to a channel."""
        if channel not in self._connections:
            return
        dead = set()
        payload = json.dumps(data, default=str)
        for ws in self._connections[channel]:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections[channel].discard(ws)

    async def broadcast_market(self, symbol: str, price: float, bid: float, ask: float,
                               change_pct: float = 0):
        await self.broadcast("market", {
            "type": "ticker",
            "symbol": symbol,
            "price": price,
            "bid": bid,
            "ask": ask,
            "change_pct": change_pct,
        })

    async def broadcast_account(self, account_data: dict):
        await self.broadcast("account", {
            "type": "account",
            "data": account_data,
        })

    @property
    def market_connections(self) -> int:
        return len(self._connections.get("market", set()))

    @property
    def account_connections(self) -> int:
        return len(self._connections.get("account", set()))


# Global singleton
ws_manager = WebSocketManager()
