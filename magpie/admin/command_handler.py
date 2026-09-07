"""Admin 指令：解析 `内容->目标` 格式并执行。

历史说明：旧版这里有一个"截图对比 + 红点检测"的管理消息监控循环 —— 但它
只打日志、从不 OCR 解析也从不执行指令（半成品空壳，还抄了第三份 GDI 截图
代码）。v5.0 起移除：指令入口统一为 Web API `POST /api/admin/command`（或
直接调 :func:`parse_command` + :meth:`AdminCommandHandler.execute_command_text`）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..core.config import AdminConfig
from ..core.store import MessageStore
from ..core.ui_engine import UIEngine

logger = logging.getLogger(__name__)

COMMAND_PATTERN = re.compile(
    r"^(?P<content>.+?)->(?P<target>\S+)\s*$",
    re.DOTALL,
)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


@dataclass
class ParsedCommand:
    content_type: str
    content: str
    target: str


def parse_command(text: str) -> Optional[ParsedCommand]:
    """Parse admin command text."""
    text = text.strip()

    if text.startswith("/image "):
        rest = text[7:].strip()
        match = COMMAND_PATTERN.match(rest)
        if match:
            return ParsedCommand(
                content_type="image",
                content=match.group("content").strip(),
                target=match.group("target").strip(),
            )
        return None

    if text.startswith("/text "):
        rest = text[6:].strip()
        match = COMMAND_PATTERN.match(rest)
        if match:
            return ParsedCommand(
                content_type="text",
                content=match.group("content").strip(),
                target=match.group("target").strip(),
            )
        return None

    match = COMMAND_PATTERN.match(text)
    if match:
        content = match.group("content").strip()
        target = match.group("target").strip()
        if any(content.lower().endswith(ext) for ext in IMAGE_EXTS):
            return ParsedCommand(content_type="image", content=content, target=target)
        return ParsedCommand(content_type="text", content=content, target=target)

    return None


class AdminCommandHandler:
    """执行管理指令（发送文本/图片到指定会话并记账）。"""

    def __init__(
        self,
        config: AdminConfig,
        engine: UIEngine,
        msg_logger: MessageStore,
    ) -> None:
        self.config = config
        self.engine = engine
        self.msg_logger = msg_logger
        self._running = False

    async def start(self) -> None:
        """校验配置并标记运行（真正的指令从 Web API 进来，无需后台轮询）。"""
        if not self.config.enabled:
            logger.info("Admin handler disabled")
            self._running = False
            return
        if not self.config.admin_contacts:
            logger.warning("Admin handler enabled but no admin contacts configured")
            self._running = False
            return
        self._running = True
        logger.info("Admin handler ready（指令入口：POST /api/admin/command）")

    async def stop(self) -> None:
        self._running = False

    async def execute_command_text(self, command_text: str) -> dict:
        """Execute a command from web API."""
        cmd = parse_command(command_text)
        if not cmd:
            return {"success": False, "error": "Invalid command format"}

        logger.info("Executing command: %s -> %s (%s)", cmd.content[:30], cmd.target, cmd.content_type)
        return await self._execute_command(cmd)

    async def _execute_command(self, cmd: ParsedCommand) -> dict:
        """Execute a parsed admin command."""
        try:
            if cmd.content_type == "text":
                success = await self.engine.send_text(cmd.target, cmd.content)
                self.msg_logger.log_send(
                    cmd.target, "text", cmd.content,
                    status="success" if success else "failed",
                )
                logger.info("Admin command executed: text to %s, success=%s", cmd.target, success)
                return {"success": success, "target": cmd.target, "type": "text"}

            elif cmd.content_type == "image":
                success = await self.engine.send_image_file(cmd.target, cmd.content)
                self.msg_logger.log_send(
                    cmd.target, "image", cmd.content,
                    status="success" if success else "failed",
                )
                logger.info("Admin command executed: image to %s, success=%s", cmd.target, success)
                return {"success": success, "target": cmd.target, "type": "image"}

            else:
                logger.warning("Unknown content type: %s", cmd.content_type)
                return {"success": False, "error": f"Unknown content type: {cmd.content_type}"}

        except Exception as e:
            logger.exception("Failed to execute admin command")
            self.msg_logger.log_send(
                cmd.target, cmd.content_type, cmd.content, status="error", error=str(e),
            )
            return {"success": False, "error": str(e)}
