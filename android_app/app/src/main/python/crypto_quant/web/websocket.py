"""
WebSocket Manager - Real-time data streaming for the trading dashboard
"""
import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts real-time updates."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {
            "market": set(),   # Price/ticker updates
            "account": set(),  # Account/position changes
        }

    async def connect(self, websocket: WebSocket, channel: str = "market"):
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)
        logger.info(f"WebSocket connected to {channel} (total: {len(self._connections[channel])})")

    def disconnect(self, websocket: WebSocket, channel: str = "market"):
        if channel in self._connections:
            self._connections[channel].discard(websocket)
        logger.info(f"WebSocket disconnected from {channel}")

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
