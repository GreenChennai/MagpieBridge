"""UI Automation engine - high-level operations combining all core modules."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .clipboard import Clipboard
from .config import AppConfig
from .human_simulator import HumanSimulator
from .op_queue import OperationQueue
from .wechat_adapter import WeChatAdapter
from .wechat_adapter_4x import WeChat4xAdapter
from .window_manager import WindowManager
from ..tui import bus

logger = logging.getLogger(__name__)


class UIEngine:
    """High-level UI automation engine for WeChat operations."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.human = HumanSimulator(config.human_sim)
        self._try_load_human_profile()
        self.window = WindowManager()
        self.clipboard = Clipboard()
        self.adapter: WeChatAdapter = WeChat4xAdapter(self.human, config.wechat)
        self._initialized = False
        # Serialized WeChat UI operation queue (one send at a time).
        self.op_queue = OperationQueue()

    def _try_load_human_profile(self) -> None:
        """Load a recorded human-behavior profile (from tools/human_behavior_recorder)
        if one exists next to the exe, so the simulated mouse uses real measured
        speed / tremor / pauses instead of hard-coded guesses."""
        try:
            import sys
            from pathlib import Path
            base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent
            for p in (base / "human_profile.json", base / "tools" / "human_profile.json"):
                if p.exists():
                    if self.human.load_profile(str(p)):
                        logger.info("已加载真人行为画像: %s", p)
                        break
        except Exception:
            pass

    async def initialize(self) -> bool:
        """Initialize the engine: find window, lock position, verify adapter, scan baseline."""
        try:
            hwnd = self.window.find_window()
            if not hwnd:
                # Exception recovery: auto-launch WeChat if it is not running
                if self._launch_wechat():
                    logger.info("已自动启动微信，等待窗口出现...")
                    for _ in range(12):
                        await asyncio.sleep(1)
                        hwnd = self.window.find_window()
                        if hwnd:
                            break
            if not hwnd:
                logger.error("初始化时未找到微信窗口")
                return False

            wc = self.config.wechat
            self.window.set_position(wc.window_position_x, wc.window_position_y, wc.window_width, wc.window_height)
            if not self.window.restore_and_focus():
                # 启动时窗口可能在托盘里：尝试过就继续（find_window 仍可用），
                # 真正的硬校验在每次 _send_*_sync 前的 _focus_wechat()
                logger.warning("启动时微信窗口未能置前（可能隐藏在托盘），首次发送前将再次尝试恢复")

            if not self.adapter.find_chat_list():
                logger.warning("未找到聊天列表，适配器可能需要重新校准")

            # wxauto-style version check (warn when WeChat updated)
            try:
                self.adapter.check_version()
            except Exception:
                pass

            # NOTE: chat-list baseline (scan_baseline) is now LAZY - it only
            # runs when get_chat_list_items() is actually requested, so startup
            # does not walk the whole contact list.
            self._initialized = True
            logger.info("UI 引擎初始化成功")
            return True
        except Exception:
            logger.exception("UI 引擎初始化失败")
            return False

    @staticmethod
    def _launch_wechat() -> bool:
        """Try to launch WeChat from common install paths (recovery strategy).

        Weixin.exe (v4.x) takes priority over the legacy WeChat.exe (v3.x).
        """
        import os
        candidates = [
            r"C:\Program Files\Tencent\Weixin\Weixin.exe",
            r"C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
            r"C:\Program Files\Tencent\WeChat\WeChat.exe",
            r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    os.startfile(path)
                    logger.info("自动启动微信: %s", path)
                    return True
                except Exception:
                    logger.exception("自动启动微信失败: %s", path)
        return False

    async def send_text(self, contact: str, text: str) -> bool:
        """Send a text message to a contact or group (serialized via the queue)."""
        return await self.send_text_state(contact, text) == "sent"

    async def send_text_state(self, contact: str, text: str) -> str:
        """Send text and report the *delivery state*, not just success.

        Returns:
            "sent"      确认已发送（UI 校验通过）
            "failed"    确认未发出（找不到会话 / 守卫拦截 / 按钮仍绿）→ 调用方可安全重试这一步
            "uncertain" 结果不确定（操作超时，消息可能稍后才发出）→ **禁止盲目重发**
        """
        return await self._submit_state(
            "发送", "文本", contact, text[:60],
            lambda: self._send_text_sync(contact, text),
            lambda e: f"内容: {text[:40]}" + (f"｜{e}" if e else ""),
        )

    async def _submit_state(self, kind: str, label: str, contact: str,
                            detail: str, job, alert_detail) -> str:
        """One serialized UI send; maps outcome/exception to sent|failed|uncertain."""
        if not self._initialized:
            await self.initialize()
        try:
            ok = await self.op_queue.submit(kind, contact, detail, job)
            if ok:
                return "sent"
            await self._alert_failure(label, contact, alert_detail(None))
            return "failed"
        except asyncio.TimeoutError:
            # 队列超时：UI 动作可能仍在执行（消息可能已/稍后会发出），只告警不判死
            logger.exception("向 %s 发送%s超时（结果不确定，勿盲目重发）", contact, label)
            await self._alert_failure(label, contact, alert_detail("操作超时，结果不确定"))
            return "uncertain"
        except Exception as e:
            logger.exception("向 %s 发送%s失败", contact, label)
            await self._alert_failure(label, contact, alert_detail(str(e)))
            return "failed"

    def _send_text_sync(self, contact: str, text: str) -> bool:
        """Synchronous WeChat text-send (run inside the queue worker thread)."""
        if not self._focus_wechat():
            return False
        self.human.sync_delay()
        if not self.adapter.search_contact(contact):
            logger.error("未找到联系人: %s", contact)
            return False
        self.human.sync_delay()
        # 把目标名传下去：适配器会在按 Enter 前复查当前会话是否仍是 contact
        return self.adapter.send_text(text, target=contact)

    def _focus_wechat(self) -> bool:
        """Restore+foreground WeChat and refuse to blind-type when that fails.

        Without this guard, a tray-hidden WeChat makes every synthetic
        click/keystroke land on the user's currently focused window while the
        OCR content checks spin in retry loops until the job times out.
        """
        from ..tui import bus
        bus.wait_yield_release()   # 延时操作：让出控制权期间不抢前台
        # 远程桌面断开后会话可能已断开/锁屏，合成输入失效 → 发送必失败。
        # 每次发送前先确保会话可交互（必要时自动挂回控制台并等它激活），
        # 消除"关闭远程桌面后发送必失败"的窗口。会话活跃时不干扰用户。
        try:
            from .session_control import ensure_sendable_session
            if not ensure_sendable_session():
                logger.error("会话不可交互（远程桌面已断开且未能挂回控制台），放弃本次发送")
                return False
        except Exception:
            pass
        if not self.window.restore_and_focus():
            logger.error("微信窗口不可见/无法置前（可能被隐藏到托盘），放弃本次发送；"
                         "请在托盘恢复微信主界面后重试")
            return False
        return True

    async def send_image_file(self, contact: str, file_path: str) -> bool:
        """Send an image file to a contact or group (serialized via the queue)."""
        return await self.send_image_file_state(contact, file_path) == "sent"

    async def send_image_file_state(self, contact: str, file_path: str) -> str:
        return await self._submit_state(
            "发送图片", "图片", contact, file_path,
            lambda: self._send_image_file_sync(contact, file_path),
            lambda e: f"文件: {file_path}" + (f"｜{e}" if e else ""),
        )

    def _send_image_file_sync(self, contact: str, file_path: str) -> bool:
        if not self._focus_wechat():
            return False
        self.human.sync_delay()
        if not self.adapter.search_contact(contact):
            logger.error("未找到联系人: %s", contact)
            return False
        self.human.sync_delay()
        return self.adapter.send_image_from_file(file_path, target=contact)

    async def send_image_bytes(self, contact: str, data: bytes, filename: str = "image.png") -> bool:
        """Send image bytes to a contact or group (serialized via the queue)."""
        return await self.send_image_bytes_state(contact, data, filename) == "sent"

    async def send_image_bytes_state(self, contact: str, data: bytes, filename: str = "image.png") -> str:
        return await self._submit_state(
            "发送图片", "图片", contact, filename,
            lambda: self._send_image_bytes_sync(contact, data, filename),
            lambda e: f"文件: {filename}" + (f"｜{e}" if e else ""),
        )

    def _send_image_bytes_sync(self, contact: str, data: bytes, filename: str) -> bool:
        if not self._focus_wechat():
            return False
        self.human.sync_delay()
        if not self.adapter.search_contact(contact):
            logger.error("未找到联系人: %s", contact)
            return False
        self.human.sync_delay()
        return self.adapter.send_image_from_bytes(data, filename, target=contact)

    async def send_at_message(self, contact: str, at_name: str, text: str) -> bool:
        """Send an @mention message to a contact or group (serialized)."""
        return await self.send_at_message_state(contact, at_name, text) == "sent"

    async def send_at_message_state(self, contact: str, at_name: str, text: str) -> str:
        return await self._submit_state(
            "发送@", "@消息", contact, f"@{at_name} {text[:40]}",
            lambda: self._send_at_sync(contact, at_name, text),
            lambda e: f"@ {at_name} 内容: {text[:40]}" + (f"｜{e}" if e else ""),
        )

    def _send_at_sync(self, contact: str, at_name: str, text: str) -> bool:
        if not self._focus_wechat():
            return False
        self.human.sync_delay()
        if not self.adapter.search_contact(contact):
            logger.error("未找到联系人: %s", contact)
            return False
        self.human.sync_delay()
        return self.adapter.send_at(text, at_name, target=contact)

    async def _alert_failure(self, kind: str, target: str, detail: str) -> None:
        """发送失败告警（经全局 alerter，未配置时静默跳过）。"""
        try:
            from .alerter import get_alerter
            await get_alerter().notify_send_failure(kind, target, detail)
        except Exception:
            logger.exception("发送失败告警触发异常")

    def get_chat_list(self) -> list[dict[str, str]]:
        """Get visible chat list items."""
        return self.adapter.get_chat_list_items()

    def get_status(self) -> dict:
        """Return engine status (cheap - no window scanning)."""
        return {
            "initialized": self._initialized,
            "wechat_version": self.adapter.version,
            "window_found": self.window.find_window() is not None,
            "chat_list_found": self.adapter.has_chat_list(),
            "current_chat": self.adapter.get_current_chat_name(),
        }

