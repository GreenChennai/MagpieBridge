"""OneBot v11 HTTP 适配端 —— 只管 HTTP 语义（路由/鉴权/编解码）。

action 语义在 :mod:`.dispatcher`（HTTP/WS 共用）；runner 生命周期在
``start/stop`` 里自己保存引用（旧版 ``self._app.runners`` 在 aiohttp 3.x
不存在，stop() 必炸 AttributeError —— 潜伏地雷已拆除）。
"""

from __future__ import annotations

import logging

from aiohttp import web

from ..core.ui_engine import UIEngine
from .dispatcher import ActionError, OneBotDispatcher
from .models import OneBotResponse, StatusResponse

logger = logging.getLogger(__name__)


class OneBotHTTP:
    """OneBot v11 HTTP server implementation."""

    def __init__(self, engine: UIEngine, host: str = "127.0.0.1", port: int = 3000, token: str = "") -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self.token = token
        self.dispatcher = OneBotDispatcher(engine)
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    @property
    def plugin_manager(self):  # 兼容旧调用方（main.py set_plugin_manager 之外偶有读取）
        return self.dispatcher.plugin_manager

    def set_plugin_manager(self, pm) -> None:
        """Attach the plugin manager (plugins may provide custom actions)."""
        self.dispatcher.set_plugin_manager(pm)

    def _setup_routes(self) -> None:
        self._app.router.add_post("/", self._handle_action)
        self._app.router.add_post("/{action}", self._handle_action)
        self._app.router.add_get("/status", self._handle_status)

    def _check_auth(self, request: web.Request) -> bool:
        if not self.token:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    async def _handle_action(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response(
                OneBotResponse(status="failed", retcode=100, data="Unauthorized").model_dump()
            )
        try:
            body = await request.json()
            action = body.get("action", request.match_info.get("action", ""))
            params = body.get("params", {})
            echo = body.get("echo")
            result = await self.dispatcher.dispatch(action, params, echo=echo)
            return web.json_response(OneBotResponse(data=result, echo=echo).model_dump())
        except ActionError as e:
            return web.json_response(
                OneBotResponse(status="failed", retcode=e.code, data=str(e)).model_dump()
            )
        except Exception as e:
            logger.exception("Error handling action")
            return web.json_response(
                OneBotResponse(status="failed", retcode=200, data=str(e)).model_dump()
            )

    async def _handle_status(self, request: web.Request) -> web.Response:
        status = self.engine.get_status()
        return web.json_response(
            StatusResponse(
                online=status.get("window_found", False),
                good=status.get("initialized", False),
                stat=status,
            ).model_dump()
        )

    async def start(self) -> None:
        """Start the HTTP server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("OneBot HTTP server started on %s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
