"""OneBot action 分发器 —— HTTP 与 WebSocket 共用的唯一实现。

两个传输端（v11_http / v11_ws）只做各自擅长的事：鉴权、连接管理、编解码。
action 的语义（内置处理 + 插件 action 回退 + 审计）全部在这里（ADR-0005 去重）。

接口：``dispatch(action, params) -> Any``；失败抛 :class:`ActionError`，
由传输端转成 ``retcode != 0`` 的 OneBotResponse —— 绝不谎报成功。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..core.audit import audit
from ..core.ui_engine import UIEngine

logger = logging.getLogger(__name__)


class ActionError(Exception):
    """action 执行失败；code 对应 OneBotResponse.retcode。"""

    def __init__(self, message: str, code: int = 200):
        super().__init__(message)
        self.code = code


def _flatten_message(message: Any) -> str:
    """OneBot 消息段数组 → 纯文本（text 段拼接；其余段类型忽略）。"""
    if isinstance(message, list):
        return "".join(
            seg.get("data", {}).get("text", "") for seg in message if seg.get("type") == "text"
        )
    return str(message or "")


class OneBotDispatcher:
    """内置 action + 插件 action 的统一入口。"""

    def __init__(self, engine: UIEngine) -> None:
        self.engine = engine
        self.plugin_manager = None

    # ------------------------------------------------------------ 组装
    def set_plugin_manager(self, pm) -> None:
        self.plugin_manager = pm

    # ------------------------------------------------------------ 分发
    async def dispatch(self, action: str, params: dict, echo: Any = None) -> Any:
        params = params or {}
        handler = getattr(self, f"do_{action}", None)
        if handler is not None:
            try:
                return await handler(**params)
            except ActionError:
                raise
            except TypeError as e:
                # 参数名不匹配等：明确报错而不是 500
                raise ActionError(f"参数错误: {e}") from e
            except Exception as e:
                logger.exception("action %s 执行失败", action)
                raise ActionError(str(e)) from e

        if self.plugin_manager and self.plugin_manager.has_action(action):
            # 调试通道轮询类 action（debug_poll 每 1.5s、debug_status 每 5s）不审计——
            # 否则操作日志面板被刷屏；业务 action（send_leads/leads_check 等）正常审计
            try:
                result = await self.plugin_manager.run_action(action, **params)
            except Exception as e:
                logger.exception("插件 action %s 执行失败", action)
                raise ActionError(str(e)) from e
            if not action.startswith("debug_"):
                audit("OneBot action", action, 结果="ok", 参数数=len(params))
            return result

        raise ActionError(f"Unknown action: {action}", code=100)

    # ------------------------------------------------------------ 内置 action
    async def _send_and_ack(self, contact: str, message: str) -> dict:
        """发送并返回真实结果：失败抛异常 → 上层转成 retcode!=0，绝不谎报成功。

        旧实现丢弃 ``engine.send_text`` 的返回值、永远返回凭空生成的
        message_id —— 上游把失败当成功导致消息静默丢失；上游另行超时重试
        又会**重复发送**。
        """
        ok = await self.engine.send_text(contact, message)
        if not ok:
            logger.error("发送失败（目标校验未通过或执行出错），拒绝上报成功: %s", contact)
            raise ActionError(f"发送失败: {contact}（可能目标会话校验未通过）")
        return {"message_id": int(time.time() * 1000)}

    async def do_send_private_msg(self, user_id: int = 0, message: Any = "", **_: Any) -> dict:
        return await self._send_and_ack(str(user_id), _flatten_message(message))

    async def do_send_group_msg(self, group_id: int = 0, message: Any = "", **_: Any) -> dict:
        return await self._send_and_ack(str(group_id), _flatten_message(message))

    async def do_send_msg(
        self, message_type: str = "", user_id: int = 0, group_id: int = 0, message: Any = "", **_: Any
    ) -> dict:
        contact = str(group_id if message_type == "group" else user_id)
        return await self._send_and_ack(contact, _flatten_message(message))

    async def do_get_status(self, **_: Any) -> dict:
        return self.engine.get_status()

    async def do_get_chat_list(self, **_: Any) -> list[dict]:
        return self.engine.get_chat_list()

    async def do_get_group_list(self, **_: Any) -> list[dict]:
        # UI 自动化拿不到完整群列表：返回空列表（OneBot 语义允许）
        return []

    async def do_get_friend_list(self, **_: Any) -> list[dict]:
        return []
