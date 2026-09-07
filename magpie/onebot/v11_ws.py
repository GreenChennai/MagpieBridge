"""OneBot v11 WebSocket 适配端 —— 只管 WS 语义（连接/鉴权/编解码）。

action 语义在 :mod:`.dispatcher`（HTTP/WS 共用，约 60 行重复代码已清除）。
websockets 兼容：≥13 的新 asyncio API 优先，旧 ``WebSocketServerProtocol``
自动回退（requirements 上限未封死，避免升级即炸）。鉴权支持 Bearer 头与
``?access_token=`` 查询参数（OneBot 生态两种惯用法）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets

from ..core.ui_engine import UIEngine
from .dispatcher import ActionError, OneBotDispatcher
from .models import OneBotResponse

logger = logging.getLogger(__name__)

try:  # websockets >= 13 新 asyncio API
    from websockets.asyncio.server import ServerConnection

    _MODERN_WS = True
except ImportError:  # pragma: no cover - 旧版本回退
    from websockets.server import WebSocketServerProtocol as ServerConnection

    _MODERN_WS = False


def _ws_headers(ws: Any) -> dict:
    headers = getattr(ws, "request", None)
    if headers is not None and hasattr(headers, "headers"):  # modern
        return dict(headers.headers)
    return dict(getattr(ws, "request_headers", {}))  # legacy


def _ws_path(ws: Any) -> str:
    req = getattr(ws, "request", None)
    if req is not None and hasattr(req, "path"):
        return req.path
    return getattr(ws, "path", "")


class OneBotWebSocket:
    """OneBot v11 WebSocket server implementation."""

    def __init__(self, engine: UIEngine, host: str = "127.0.0.1", port: int = 3001, token: str = "") -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self.token = token
        self.dispatcher = OneBotDispatcher(engine)
        self._clients: set[Any] = set()
        self._server: Any = None

    @property
    def plugin_manager(self):
        return self.dispatcher.plugin_manager

    def set_plugin_manager(self, pm) -> None:
        """Attach the plugin manager (plugins may provide custom actions)."""
        self.dispatcher.set_plugin_manager(pm)

    def _check_auth(self, ws: Any) -> bool:
        if not self.token:
            return True
        if _ws_headers(ws).get("Authorization", "") == f"Bearer {self.token}":
            return True
        qs = parse_qs(urlparse(_ws_path(ws)).query)
        return qs.get("access_token", [""])[0] == self.token

    async def _handler(self, websocket: Any, *_legacy_path: Any) -> None:
        if not self._check_auth(websocket):
            await websocket.close(1008, "Unauthorized")
            return

        self._clients.add(websocket)
        logger.info("WebSocket client connected: %s", websocket.remote_address)
        try:
            async for message in websocket:
                await self._process_message(websocket, message)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)
            logger.info("WebSocket client disconnected: %s", websocket.remote_address)

    async def _process_message(self, ws: Any, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
            result = await self.dispatcher.dispatch(
                data.get("action", ""), data.get("params", {}), echo=data.get("echo")
            )
            resp = OneBotResponse(data=result, echo=data.get("echo"))
            await ws.send(resp.model_dump_json())
        except ActionError as e:
            resp = OneBotResponse(status="failed", retcode=e.code, data=str(e), echo=_echo_of(raw))
            await ws.send(resp.model_dump_json())
        except Exception as e:
            logger.exception("Error processing WebSocket message")
            resp = OneBotResponse(status="failed", retcode=200, data=str(e), echo=_echo_of(raw))
            await ws.send(resp.model_dump_json())

    async def start(self) -> None:
        """Start the WebSocket server."""
        if _MODERN_WS:
            from websockets.asyncio.server import serve

            self._server = await serve(self._handler, self.host, self.port)
        else:  # pragma: no cover - legacy 签名多一个 path 参数
            self._server = await websockets.serve(self._handler, self.host, self.port)
        logger.info("OneBot WebSocket server started on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()


def _echo_of(raw: str | bytes) -> Any:
    try:
        return json.loads(raw).get("echo")
    except Exception:
        return None
