"""Window management for WeChat - positioning, visibility, focus control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# 64-bit correctness: without explicit restypes these return c_int, and real
# HWNDs above 0x7FFFFFFF come back NEGATIVE (or NULL on failure), silently
# breaking equality comparisons against handles from FindWindowW.
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.FindWindowW.restype = ctypes.wintypes.HWND
user32.WindowFromPoint.restype = ctypes.wintypes.HWND
user32.GetAncestor.restype = ctypes.wintypes.HWND
user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
user32.GetForegroundWindow.argtypes = []
user32.GetWindowThreadProcessId.restype = ctypes.c_uint
user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.c_uint)]

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


class WindowManager:
    """Manages WeChat window position, visibility and focus."""

    def __init__(self, window_title: str = "微信") -> None:
        self.window_title = window_title
        self._hwnd: Optional[int] = None

    def find_window(self) -> Optional[int]:
        """Find WeChat window handle by title."""
        hwnd = user32.FindWindowW(None, self.window_title)
        if hwnd:
            self._hwnd = hwnd
            logger.debug("Found WeChat window: hwnd=%s", hwnd)
        else:
            logger.debug("WeChat window not found")
        return hwnd

    def ensure_found(self) -> int:
        """Find window or raise if not found."""
        hwnd = self.find_window()
        if not hwnd:
            raise RuntimeError(f"WeChat window '{self.window_title}' not found")
        return hwnd

    def set_position(self, x: int, y: int, width: int, height: int) -> bool:
        """Set window position and size."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        result = user32.SetWindowPos(hwnd, 0, x, y, width, height, SWP_NOZORDER)
        logger.info("设置窗口位置: (%d, %d, %d, %d), 结果=%s", x, y, width, height, result)
        return bool(result)

    def set_topmost(self, topmost: bool = True) -> bool:
        """Set window always-on-top."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        flag = HWND_TOPMOST if topmost else HWND_NOTOPMOST
        result = user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
        logger.info("Set topmost: %s, result=%s", topmost, result)
        return bool(result)

    def bring_to_front(self) -> bool:
        """Bring window to foreground."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        return True

    def _force_foreground(self, hwnd: int) -> bool:
        """把 hwnd 强抬到前台（绕过 Windows 前台锁）。

        普通 SetForegroundWindow 经常被静默拒绝（返回 0、foreground 仍是
        0/别的窗口），典型场景就是后台自动化进程。经典解法：把自己线程
        Attach 到前台线程，借它的名义抬窗口。
        """
        kernel32 = ctypes.windll.kernel32
        fg = user32.GetForegroundWindow()
        ft = user32.GetWindowThreadProcessId(fg or hwnd, None)
        ct = kernel32.GetCurrentThreadId()
        attached = False
        if ft and ft != ct:
            try:
                attached = bool(user32.AttachThreadInput(ct, ft, True))
            except Exception:
                attached = False
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            ok = user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(ct, ft, False)
                except Exception:
                    pass
        return bool(ok)

    def _click_titlebar_to_focus(self, hwnd: int) -> bool:
        """最后手段：物理鼠标点击标题栏空白处强制激活窗口。

        SetForegroundWindow 会被"前台锁"拒绝（后台自动化进程抬窗返回 0，
        GetForegroundWindow 恒为 NULL 的桌面就是这种状态）。但 Windows 对
        **鼠标点击非活动窗口**不做前台锁限制 —— 点一下微信标题栏空白区即可
        把它带到前台，是绕过前台锁最可靠的办法。点击点取标题栏水平中部，
        避开左侧头像/右侧 固定·最小化·关闭 按钮。
        """
        rect = self.get_window_rect()
        if not rect:
            return False
        x, y, w, h = rect
        cx = x + int(w * 0.5)
        cy = y + 16
        try:
            pt = user32.GetForegroundWindow()
            if pt == hwnd:
                return True
            user32.SetCursorPos(cx, cy)
            time.sleep(0.06)
            user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
            time.sleep(0.06)
            user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP
            time.sleep(0.3)
            return user32.GetForegroundWindow() == hwnd
        except Exception:
            logger.exception("_click_titlebar_to_focus 异常")
            return False

    def restore_and_focus(self) -> bool:
        """Un-hide / un-minimize the window, put it foreground, and VERIFY it.

        WeChat can vanish behind other windows, get minimized, or be hidden to
        the system tray (its global Ctrl+Alt+W "hide/show" toggle, a user key
        press, or WeChat itself).  When that happens `bring_to_front()` is a
        silent no-op: SetForegroundWindow on a hidden window does nothing, and
        every subsequent synthetic click/keystroke lands on whatever window the
        USER has focused -- typing text into the wrong app and making the OCR
        diff guards spin until the operation times out.

        This method restores visibility (SW_RESTORE / SW_SHOW), retries the
        foreground handoff with the standard Alt-tap unlock trick, and only then
        reports success.  Callers MUST abort the send when it returns False
        rather than blind-typing.
        """
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        SW_RESTORE, SW_SHOW = 9, 5
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            elif not user32.IsWindowVisible(hwnd):
                # 隐藏到托盘：SW_RESTORE/SW_SHOW 清掉 hidden 状态
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.ShowWindow(hwnd, SW_SHOW)
            time.sleep(0.25)
            got = False
            for attempt in range(3):
                if self._force_foreground(hwnd):
                    got = True
                    break
                # Windows 前台锁定：轻按 Alt 解除后重试（无修饰键组合，不触发任何热键）
                user32.keybd_event(0x12, 0, 0, 0)      # VK_MENU down
                user32.keybd_event(0x12, 0, 2, 0)      # VK_MENU up
                time.sleep(0.15)
            if not got:
                # API 全被前台锁拒绝 → 物理鼠标点击标题栏（点击不受前台锁限制）
                got = self._click_titlebar_to_focus(hwnd)
            time.sleep(0.15)
            visible = bool(user32.IsWindowVisible(hwnd))
            fg = user32.GetForegroundWindow()
            # 会话桌面失去前台（RDP 断开、锁屏、LogonUI 抢占）时，所有注入的
            # 点击/按键都会石沉大海 —— 此时绝不能"宽容放行"，必须让上层快速
            # 失败并告警，否则会演变成"发了但没到 + 反复重试"的刷屏事故。
            ok = visible and fg == hwnd
            if not ok:
                logger.warning("微信窗口未能置前: visible=%s foreground=%s hwnd=%s "
                               "（若长时间如此，请检查 RDP 是否断开/屏幕是否锁定——"
                               "会话失去活动桌面后按键注入无效）", visible, fg, hwnd)
            else:
                self._hwnd = hwnd
            return ok
        except Exception:
            logger.exception("restore_and_focus 异常")
            return False

    def is_window_visible(self) -> bool:
        """Check if window is visible."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return False
        return bool(user32.IsWindowVisible(hwnd))

    def get_window_rect(self) -> Optional[tuple[int, int, int, int]]:
        """Get window position and size as (x, y, width, height)."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)

    def is_covered(self) -> bool:
        """Check if another window fully covers the WeChat window."""
        hwnd = self._hwnd or self.find_window()
        if not hwnd:
            return True
        rect = self.get_window_rect()
        if not rect:
            return True
        x, y, w, h = rect
        center_x = x + w // 2
        center_y = y + h // 2
        top_hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(center_x, center_y))
        return top_hwnd != hwnd

    def lock_position(self, x: int, y: int, width: int, height: int) -> None:
        """Lock window to specific position (call periodically)."""
        current = self.get_window_rect()
        if current and current != (x, y, width, height):
            logger.warning("窗口被移动，正在复位: %s -> (%d,%d,%d,%d)", current, x, y, width, height)
            self.set_position(x, y, width, height)
