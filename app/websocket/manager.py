"""
websocket/manager.py — WebSocket connection manager.

Maintains the set of active connections and broadcasts JSON messages
to all clients whenever key events occur:
  • log_ingested       — new log entry stored in DB
  • analysis_ready     — AI analysis complete
  • playbook_created   — new playbook generated
  • step_completed     — a playbook step was checked off
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        print(f"[ws] Client connected. Total connections: {len(self._connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            try:
                self._connections.remove(websocket)
            except ValueError:
                pass
        print(f"[ws] Client disconnected. Total connections: {len(self._connections)}")

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected client; silently drop dead connections."""
        if not self._connections:
            return

        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []

        async with self._lock:
            connections_snapshot = list(self._connections)

        for ws in connections_snapshot:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    try:
                        self._connections.remove(ws)
                    except ValueError:
                        pass

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Singleton — imported by routes and background tasks
manager = ConnectionManager()
