"""MagpieBridge - main entry point."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

import uvicorn

from magpie.admin.command_handler import AdminCommandHandler
from magpie.core.config import AppConfig
from magpie.core.hotkey import GlobalHotkey
from magpie.core.ui_engine import UIEngine
from magpie.monitor.listener import WeChatListener
from magpie.core.store import MessageStore
from magpie.monitor.status import StatusMonitor
from magpie.onebot.v11_http import OneBotHTTP
from magpie.onebot.v11_ws import OneBotWebSocket
from magpie.tui import MagpieTui, bus
from magpie.tui.log_handler import TuiLogHandler
from magpie.web_server import create_app


def get_base_dir() -> Path:
    """Get base directory - works for both dev and PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _enable_dpi_awareness() -> str:
    """Make the process Per-Monitor DPI aware so every pixel coordinate passed
    to SetWindowPos / GetMonitorInfo / GetWindowRect is a REAL physical pixel.

    Without this the PyInstaller exe is DPI-UNAWARE (its default manifest has
    no dpiAware declaration), and on machines with 125%/150% display scaling
    Windows virtualizes our coordinates: SetWindowPos(1200, 850) actually moves
    the window to 1500x1062 physical px, while GetWindowRect "confirms" 1200x850
    back in virtualized units — the size AND position look right in code/logs
    but are wrong on screen.  The sandbox runs at 100% scaling so it never
    caught this; the user's real machine did.

    Must be called BEFORE any window/DPI-dependent API (first thing in main()).
    Since the manifest declares nothing, the process starts DPI-unaware and
    this first call succeeds (later calls fail with ERROR_ACCESS_DENIED).

    Returns a diagnostic string for the log.
    """
    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT: PER_MONITOR_AWARE_V2 = -4, PER_MONITOR_AWARE = -3,
        # SYSTEM_AWARE = -2.  Values are HANDLE-like; pass as void*.
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        for name, ctx in (("PerMonitorV2", -4), ("PerMonitor", -3), ("SystemAware", -2)):
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(ctx)):
                return f"DPI={name}（物理像素）"
        # Fallback for very old builds: SetProcessDPIAware (System DPI aware)
        user32.SetProcessDPIAware.argtypes = []
        user32.SetProcessDPIAware.restype = ctypes.c_int
        if user32.SetProcessDPIAware():
            return "DPI=SystemAware(SetProcessDPIAware)"
    except Exception as e:  # pragma: no cover - no console in headless tests
        return f"DPI=FAIL({e})"
    return "DPI=UNAWARE（危险：缩放下坐标会被放大）"


def _is_admin() -> bool:
    """当前进程是否以管理员权限运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _relaunch_as_admin() -> None:
    """用 runas 以管理员身份重新启动自身（会弹一次 UAC）。"""
    try:
        import subprocess
        if getattr(sys, "frozen", False):
            exe = sys.executable
            args = [exe] + sys.argv[1:]
            # runas 需要完整命令行参数；用 ShellExecuteW 带 runas 动词
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, " ".join(f'"{a}"' for a in sys.argv[1:]) or "", None, 1,
            )
            if rc > 32:
                sys.exit(0)
        else:
            # 开发环境：用 python -m 重新拉起
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable,
                f'"{Path(__file__).resolve()}" ' + " ".join(f'"{a}"' for a in sys.argv[1:]),
                None, 1,
            )
            if rc > 32:
                sys.exit(0)
        print("提权启动失败（未获管理员授权），退出", file=sys.stderr)
    except Exception as e:
        print(f"提权启动异常: {e}", file=sys.stderr)
    sys.exit(1)


def _acquire_single_instance() -> bool:
    """Single-instance guard via a named mutex.

    Returns True when WE are the owner (first instance).  Returns False when
    another instance already holds the mutex (double-clicked twice) — the
    caller should log and exit immediately so two processes never fight over
    ports 3000/3001/8080 or run two full-screen TUIs at once.
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        kernel32.CreateMutexW(None, False, "Local\\MagpieBridge_SingleInstance")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True  # never block startup on guard failure


def _get_console_output_handle():
    """Return a REAL console screen-buffer handle for the conhost window.

    GetStdHandle(STD_OUTPUT_HANDLE) only returns a console handle when the
    process stdout is NOT redirected.  When the exe is launched with its
    output piped (automation, IDEs, CI), the handle is a PIPE and
    SetConsoleScreenBufferSize / SetCurrentConsoleFontEx fail silently —
    which made smoke tests look "all good" while the real machine (double
    click, real console) behaved differently.  Fall back to CreateFileW on
    CONOUT$ to always get the actual console buffer.
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.GetFileType.argtypes = [ctypes.c_void_p]
        kernel32.GetFileType.restype = ctypes.c_uint
        h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if h and h != ctypes.c_void_p(-1).value:
            # FILE_TYPE_CHAR (0x2) = console; FILE_TYPE_PIPE (0x3) = redirected
            if kernel32.GetFileType(h) == 0x2:
                return h
        # stdout redirected: open the console output device directly
        # GENERIC_READ|GENERIC_WRITE, FILE_SHARE_READ|FILE_SHARE_WRITE,
        # OPEN_EXISTING
        kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        h2 = kernel32.CreateFileW(
            "CONOUT$", 0xC0000000, 0x7, None, 3, 0, None)
        if h2 and h2 != wintypes.HANDLE(-1).value:
            return h2
    except Exception:
        pass
    return None


def _detect_terminal() -> str:
    """Detect which console host is running us: 'wt' (Windows Terminal /
    conpty), 'conhost' (classic console window) or 'headless' (no console).

    This matters because EVERY console-window API behaves differently:

    * conhost  — GetConsoleWindow() returns the real ConsoleWindowClass
      window; SetCurrentConsoleFontEx / SetConsoleScreenBufferSize /
      SetWindowPos all work as documented.
    * Windows Terminal (conpty) — GetConsoleWindow() returns a
      PseudoConsoleWindow placeholder (0x0).  Font / buffer APIs silently
      do nothing, and SetWindowPos on the placeholder is a no-op.  The
      window the user actually SEES is the CASCADIA_HOSTING_WINDOW_CLASS
      host window, reachable via GetParent(GetConsoleWindow()).
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return "headless"
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        name = cls.value or ""
        if name == "PseudoConsoleWindow":
            return "wt"
        if name == "ConsoleWindowClass":
            return "conhost"
        return "conhost"  # unknown host: use the classic path
    except Exception:
        return "conhost"


def _get_wt_window():
    """The REAL host window of a Windows Terminal session.

    GetConsoleWindow() under conpty returns a PseudoConsoleWindow (0x0,
    nothing visible).  The window the user sees is its CASCADIA host:
    GetParent(GetConsoleWindow()) gives us the exact CASCADIA_HOSTING_
    WINDOW_CLASS window that owns our tab.  Falls back to enumerating
    visible CASCADIA windows (preferring the foreground one) for safety.
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND

        def is_cascadia(h):
            if not h:
                return False
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            return cls.value == "CASCADIA_HOSTING_WINDOW_CLASS"

        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            parent = user32.GetParent(hwnd)
            if is_cascadia(parent):
                return parent
        # Fallback: enumerate visible CASCADIA windows (prefer foreground).
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        fg = user32.GetForegroundWindow()
        if is_cascadia(fg):
            return fg
        found = []
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hw, lp):
            if user32.IsWindowVisible(hw) and is_cascadia(hw):
                found.append(hw)
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else None
    except Exception:
        return None


# 微信窗口矩形（x, y, w, h）。main() 里 config 加载后写入，供控制台窗口定位时
# 避开微信窗口用。控制台（TUI）窗口原本固定到右上角 1200x850 —— 当用远程桌面
# 1440p 连接、断开后分辨率降回 1080p/1920 时，1200 宽的控制台会盖住位于左上角
# 的微信窗口，点击/粘贴/截图全部命中控制台而不是微信（"断开远程桌面后无法发送、
# 息屏发送时粘贴进别的聊天窗口"的根因之一）。
_WECHAT_RECT: tuple[int, int, int, int] = (0, 0, 900, 600)


def _set_wechat_rect(x: int, y: int, w: int, h: int) -> None:
    global _WECHAT_RECT
    _WECHAT_RECT = (int(x), int(y), int(w), int(h))


def _get_wechat_rect() -> tuple[int, int, int, int]:
    return _WECHAT_RECT


def _compute_console_target(work, width: int, height: int) -> tuple[int, int]:
    """计算控制台窗口的 (x, y)，确保不与微信窗口重叠。

    优先把控制台放到微信窗口**右侧**（远离左上角的微信）；若屏幕宽度不够则放到
    微信**下方**；实在放不下才退回右上角（此时控制台在微信背后，点击仍到微信）。
    这样即使分辨率在 RDP 断开后变小，控制台也不会盖住微信。
    """
    wx, wy, ww, wh = _WECHAT_RECT
    margin = 8
    # 1) 优先：右上角，但左缘至少越过微信右缘
    x = max(work.left, wx + ww + margin, work.right - 1 - width)
    y = work.top + 1
    if x + width <= work.right - 1:
        return x, y
    # 2) 次选：微信下方
    x = work.left + margin
    y = max(work.top + 1, wy + wh + margin)
    if y + height <= work.bottom - 1:
        return x, y
    # 3) 兜底：右上角（可能在微信背后，但微信被 restore_and_focus 置前）
    return max(work.left, work.right - 1 - width), work.top + 1


def _pin_wt_window(width: int = 800, height: int = 520, force_size: bool = True) -> bool:
    """Windows Terminal mode: size + pin the REAL host window clear of WeChat.

    Under conpty the classic font/buffer/size calls are no-ops, so this is
    the ONLY thing that works: SetWindowPos on the CASCADIA host window.
    Resizing the host window makes conpty update its screen buffer, and
    Textual follows automatically — no font/buffer calls needed, which is
    also what keeps the launch from flickering.

    `force_size=True` sets both size and position; `force_size=False` only
    re-pins the position (keeps whatever size the user set).
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL

        hwnd = _get_wt_window()
        if not hwnd:
            logging.getLogger("magpie").warning("WT 宿主窗口未找到，跳过窗口设置")
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            work = wintypes.RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        else:
            work = info.rcWork  # excludes taskbar

        target_x, target_y = _compute_console_target(work, width, height)

        flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        cur_w, cur_h = rect.right - rect.left, rect.bottom - rect.top
        pos_ok = abs(rect.left - target_x) <= 4 and abs(rect.top - target_y) <= 4
        size_ok = (not force_size) or (abs(cur_w - width) <= 24 and abs(cur_h - height) <= 24)
        if pos_ok and size_ok:
            # already correct — skip the SetWindowPos entirely (a same-rect
            # SetWindowPos can still force a repaint on some hosts = flicker).
            # Deliberately NO log line here: the 4s _console_loop would
            # otherwise spam "WT 窗口 ✓" every 4 seconds into the log panel,
            # which itself reads as "log panel keeps jumping".
            return True

        if force_size:
            user32.SetWindowPos(hwnd, 0, target_x, target_y, width, height, flags)
        else:
            user32.SetWindowPos(hwnd, 0, target_x, target_y, cur_w, cur_h, flags | 0x0001)

        # read back + log (real machine diagnosis lives in the log file)
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            cur_w, cur_h = rect.right - rect.left, rect.bottom - rect.top
            ok = (abs(cur_w - width) <= 24 and abs(cur_h - height) <= 24
                  and abs(rect.left - target_x) <= 4 and abs(rect.top - target_y) <= 4)
            if ok:
                logging.getLogger("magpie").info(
                    "WT 窗口: 目标 %dx%d @ (%d,%d) → 实际 %dx%d @ (%d,%d) ✓",
                    width, height, target_x, target_y, cur_w, cur_h, rect.left, rect.top)
                return True
            logging.getLogger("magpie").warning(
                "WT 窗口: 目标 %dx%d @ (%d,%d) → 实际 %dx%d @ (%d,%d)",
                width, height, target_x, target_y, cur_w, cur_h, rect.left, rect.top)
        return False
    except Exception:
        return False


def pin_console_to_top_right() -> None:
    """Pin the console window to the top-right corner of its monitor and
    disable the IME so TUI shortcuts (q/r/s) aren't swallowed as IME input.
    Also sizes the console to the default 1200x850 px window.

    DISPATCHES BY TERMINAL HOST:
    * Windows Terminal (conpty): font/buffer APIs are no-ops; we set the
      real CASCADIA host window (size + position) via SetWindowPos only.
    * Classic conhost: font first (conhost derives the window pixel size
      from (character grid x font pixels), so changing the font after
      SetWindowPos makes conhost recompute and clobber the 1200x850 size),
      then buffer + window size, then position.

    Only called during early startup (BEFORE the Textual TUI takes over the
    full screen): SetWindowPos / SetCurrentConsoleFontEx / (first)
    SetConsoleScreenBufferSize all force a full repaint, so running them
    while the TUI is already rendering is what makes the screen flicker on
    launch.  After the TUI is up, only the light pin (position + IME) is
    re-applied.
    """
    term = _detect_terminal()
    if term == "headless":
        return  # no console at all (automation / tests) — nothing to pin
    _set_console_title()
    if term == "wt":
        _pin_wt_window(800, 520, force_size=True)
        _disable_ime()
        return
    _set_console_font()          # 1. font first — pixel math baseline
    _set_console_size(800, 520)  # 2. buffer + window pixel size
    _move_console_to_top_right()  # 3. position (after size settled)
    _disable_ime()


def light_console_pin() -> None:
    """Lightweight pin for after the TUI has started: keep position and IME
    only.  Never touches window size, screen buffer or font here — resizing /
    re-fonting a live full-screen TUI repaints the whole screen (flicker).

    WT mode: the host window may still be re-laid-out by Windows Terminal
    while the app starts, so re-confirm size + position here (no-op when
    already correct — no repaint)."""
    term = _detect_terminal()
    if term == "headless":
        return
    _set_console_title()
    if term == "wt":
        _pin_wt_window(800, 520, force_size=True)
        _disable_ime()
        return
    _move_console_to_top_right()
    _disable_ime()


def _calc_console_grid(font_w: int, font_h: int, border_w: int, border_h: int,
                       width: int, height: int) -> tuple[int, int]:
    """Character grid (cols, rows) that fits inside a `width x height` pixel
    window, given the font cell size and the window chrome (title bar +
    borders).  Pure math, unit-testable."""
    cols = max(20, (width - border_w) // font_w)
    rows = max(10, (height - border_h) // font_h)
    return cols, rows


def _set_console_size(width: int, height: int) -> bool:
    """Set the console WINDOW pixel size (e.g. 1200x850 by default).

    Uses the canonical MSDN order: shrink the window to 1x1 -> set the screen
    buffer to the target grid -> expand the window to the grid -> SetWindowPos
    to the exact pixel size.  The buffer is sized to EXACTLY the target grid
    (not 160x900) so conhost can't grow the window to reveal extra buffer
    rows/columns and clobber the SetWindowPos result.

    Call AFTER _set_console_font: the px<->chars math depends on the font
    size, and changing the font afterwards makes conhost recompute the window
    size (overriding our SetWindowPos).

    Returns True if the final GetWindowRect matches the target (+/- 24px).
    """
    import logging

    import ctypes.wintypes as wintypes

    logger = logging.getLogger("magpie")
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", ctypes.c_short), ("Top", ctypes.c_short),
            ("Right", ctypes.c_short), ("Bottom", ctypes.c_short),
        ]

    class CONSOLE_FONT_INFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("nFont", ctypes.c_ulong),
            ("dwFontSize", COORD),
            ("FontFamily", ctypes.c_uint),
            ("FontWeight", ctypes.c_uint),
            ("FaceName", ctypes.c_wchar * 32),
        ]

    # --- explicit argtypes/restype: without these, 64-bit truncates the HWND
    # handle returned by GetConsoleWindow and every SetWindowPos below fails
    # silently (the size AND position "never take effect").
    kernel32.GetStdHandle.argtypes = [ctypes.c_uint]
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetConsoleWindow.argtypes = []
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.SetConsoleScreenBufferSize.argtypes = [ctypes.c_void_p, COORD]
    kernel32.SetConsoleScreenBufferSize.restype = ctypes.c_int
    kernel32.SetConsoleWindowInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    kernel32.SetConsoleWindowInfo.restype = ctypes.c_int
    kernel32.GetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    kernel32.GetCurrentConsoleFontEx.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL

    h_out = _get_console_output_handle()  # real buffer handle (CONOUT$ fallback)
    hwnd = kernel32.GetConsoleWindow()
    if not h_out or not hwnd:
        logger.warning("控制台尺寸设置跳过：无控制台句柄")
        return False

    # current font cell size (pixel math baseline)
    font_info = CONSOLE_FONT_INFOEX()
    font_info.cbSize = ctypes.sizeof(font_info)
    if not kernel32.GetCurrentConsoleFontEx(h_out, False, ctypes.byref(font_info)):
        font_info.dwFontSize = COORD(0, 16)
    font_h = font_info.dwFontSize.Y or 16
    font_w = font_info.dwFontSize.X or max(1, font_h // 2)

    # window chrome = window rect - client rect (title bar + borders)
    wrect, crect = wintypes.RECT(), wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(wrect)) and user32.GetClientRect(hwnd, ctypes.byref(crect)):
        border_w = (wrect.right - wrect.left) - (crect.right - crect.left)
        border_h = (wrect.bottom - wrect.top) - (crect.bottom - crect.top)
    else:
        border_w, border_h = 16, 39

    cols, rows = _calc_console_grid(font_w, font_h, border_w, border_h, width, height)
    logger.info("控制台窗口: 目标 %dx%d px（字体 %dx%d，边框 %dx%d → 字符网格 %dx%d）",
                width, height, font_w, font_h, border_w, border_h, cols, rows)

    # canonical order: shrink window (buffer can't be < window) -> buffer ->
    # window grid -> pixel-exact SetWindowPos.  Restore first so a maximized
    # console (conhost remembers its last state) doesn't swallow SetWindowPos.
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetWindowPos(hwnd, 0, 0, 0, 1, 1, 0x0002 | 0x0004 | 0x0010)
    ret_buf = kernel32.SetConsoleScreenBufferSize(h_out, COORD(cols, rows))
    sr = SMALL_RECT(0, 0, cols - 1, rows - 1)
    ret_win = kernel32.SetConsoleWindowInfo(h_out, True, ctypes.byref(sr))
    if not (ret_buf and ret_win):
        logger.warning("控制台缓冲/窗口网格设置失败（buffer ret=%d err=%d, window ret=%d）",
                       ret_buf, ctypes.get_last_error(), ret_win)
    user32.SetWindowPos(hwnd, 0, 0, 0, width, height, 0x0002 | 0x0004 | 0x0010)

    # verify by READ-BACK; log the actual rect even on success so a real
    # machine with display scaling can be diagnosed from the log file.
    cur_w = cur_h = -1
    for _ in range(2):
        if not user32.GetWindowRect(hwnd, ctypes.byref(wrect)):
            break
        cur_w = wrect.right - wrect.left
        cur_h = wrect.bottom - wrect.top
        if abs(cur_w - width) <= 24 and abs(cur_h - height) <= 24:
            logger.info("控制台窗口: 目标 %dx%d px → 实际 %dx%d px ✓", width, height, cur_w, cur_h)
            return True
        user32.SetWindowPos(hwnd, 0, 0, 0, width, height, 0x0002 | 0x0004 | 0x0010)
    logger.warning("控制台窗口尺寸 %dx%d 未确认（实际 %dx%d，err=%d）",
                   width, height, cur_w, cur_h, ctypes.get_last_error())
    return False


def _verify_console_size(width: int = 800, height: int = 520) -> None:
    """Post-startup size check (fires ~0.8s after launch, before the TUI is
    mounted): re-apply the pixel size ONLY if it drifted far off target (the
    first attempt failed).  No-op when already correct — this keeps the
    launch-flicker fix intact.  WT mode checks the real CASCADIA host window
    (which _pin_wt_window handles internally)."""
    if _detect_terminal() == "wt":
        _pin_wt_window(width, height, force_size=True)
        return
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            cur_w = rect.right - rect.left
            cur_h = rect.bottom - rect.top
            if abs(cur_w - width) <= 40 and abs(cur_h - height) <= 40:
                return  # already correct — no repaint
            try:
                import logging
                logging.getLogger("magpie").info(
                    "控制台尺寸 0.8s 校验: 目标 %dx%d，实际 %dx%d，补设一次", width, height, cur_w, cur_h)
            except Exception:
                pass
            user32.SetWindowPos(hwnd, 0, 0, 0, width, height, 0x0002 | 0x0004 | 0x0010)
    except Exception:
        pass


def _set_console_font() -> None:
    """Switch the console to a TrueType monospace font (Consolas).

    Windows 10's legacy conhost defaults to the bitmap SimSun/宋体 face for
    CJK output, which makes the TUI look blurry/old-fashioned.  Consolas
    renders ASCII crisply and conhost falls back to a modern CJK face only
    where needed.  No-op under Windows Terminal / modern terminals.
    """
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        # NOTE: SetCurrentConsoleFontEx / GetCurrentConsoleFontEx take the
        # SCREEN-BUFFER handle, NOT the HWND — and the buffer handle must be a
        # REAL console handle, not the redirected stdout pipe (see
        # _get_console_output_handle).  Passing the HWND or a pipe handle made
        # the font change silently fail.
        h_out = _get_console_output_handle()
        if not h_out:
            return

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class CONSOLE_FONT_INFOEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("nFont", ctypes.c_ulong),
                ("dwFontSize", COORD),
                ("FontFamily", ctypes.c_uint),
                ("FontWeight", ctypes.c_uint),
                ("FaceName", ctypes.c_wchar * 32),
            ]

        kernel32.SetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        kernel32.SetCurrentConsoleFontEx.restype = ctypes.c_int
        kernel32.GetCurrentConsoleFontEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        kernel32.GetCurrentConsoleFontEx.restype = ctypes.c_int

        info = CONSOLE_FONT_INFOEX()
        info.cbSize = ctypes.sizeof(info)
        info.FontFamily = 0x30  # FF_DONTCARE | TMPF_TRUETYPE
        info.FontWeight = 400
        info.FaceName = "Consolas"
        info.dwFontSize = COORD(0, 16)
        ret = kernel32.SetCurrentConsoleFontEx(h_out, False, ctypes.byref(info))

        # read back + log so real-machine issues are visible in the log file
        readback = CONSOLE_FONT_INFOEX()
        readback.cbSize = ctypes.sizeof(readback)
        if kernel32.GetCurrentConsoleFontEx(h_out, False, ctypes.byref(readback)):
            try:
                import logging
                ok = bool(ret) and readback.FaceName.strip() == "Consolas"
                logging.getLogger("magpie").info(
                    "控制台字体: 设置%s → 读回 %s %dx%dpx（ret=%d err=%d）",
                    "成功" if ok else "失败",
                    readback.FaceName or "?",
                    readback.dwFontSize.X, readback.dwFontSize.Y,
                    ret, ctypes.get_last_error(),
                )
            except Exception:
                pass
    except Exception:
        pass


def _move_console_to_top_right() -> None:
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        # explicit argtypes/restype: on 64-bit, handle-returning APIs must not
        # truncate, or the position computed below is garbage (window never moves).
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR if hasattr(wintypes, "HMONITOR") else ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL

        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return

        # A maximized console (conhost remembers its last state per window)
        # makes SetWindowPos silently no-op — restore first.
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            # fall back to the primary screen size
            work = wintypes.RECT(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        else:
            work = info.rcWork  # excludes taskbar

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top

        # Pin clear of WeChat, keep size, no z-order change, don't activate.
        # NOTE: flags must NOT contain SWP_NOMOVE (0x0002) — it silently
        # ignores X/Y and the window never moves.  0x0015 = SWP_NOSIZE(0x0001)
        # | SWP_NOZORDER(0x0004) | SWP_NOACTIVATE(0x0010).
        target_x, top = _compute_console_target(work, win_w, win_h)
        # already there → skip the call (the console loop re-runs this every 4s;
        # a same-position SetWindowPos can still trigger a repaint on old
        # conhost, which shows up as periodic flicker).
        if rect.left == target_x and rect.top == top:
            return
        user32.SetWindowPos(hwnd, 0, target_x, top, win_w, win_h, 0x0015)

        # read back + log so position issues are visible in the log file
        moved = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(moved)):
            try:
                import logging
                logging.getLogger("magpie").info(
                    "控制台位置: 目标 (%d,%d) → 实际 (%d,%d)（工作区 %dx%d）",
                    target_x, top, moved.left, moved.top,
                    work.right - work.left, work.bottom - work.top,
                )
            except Exception:
                pass
    except Exception:
        pass


def _disable_ime() -> None:
    """Disassociate the CJK IME from the console window so shortcuts work.

    Without this, pressing 'q' feeds the Chinese input method instead of the
    TUI, so Textual bindings never fire.  Under Windows Terminal the IME must
    be disabled on the CASCADIA host window (GetConsoleWindow() returns a
    placeholder); _get_wt_window() covers that, and falls back to the classic
    console window on conhost.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        imm32 = ctypes.windll.imm32
        kernel32.GetConsoleWindow.argtypes = []
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        hwnd = _get_wt_window() or kernel32.GetConsoleWindow()
        if not hwnd:
            return
        # ImmAssociateContext(hwnd, NULL) turns the IME off for this window.
        imm32.ImmAssociateContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        imm32.ImmAssociateContext.restype = ctypes.c_bool
        imm32.ImmAssociateContext(hwnd, None)
    except Exception:
        pass


def _set_console_title(title: str = "") -> None:
    """Set the CMD / console window title (e.g. "MagpieBridge v0.4.14").

    When the exe is launched from an existing CMD window the title keeps
    showing the exe path ("E:\\...\\MagpieBridge.exe") or the parent CMD's
    default title, which looks unprofessional.  This sets BOTH the classic
    console title (SetConsoleTitleW — also forwarded as an OSC 0 sequence to
    a Windows Terminal tab) and the real window title via SetWindowTextW on
    the CASCADIA host window when running under Windows Terminal.

    Called early in startup (before the TUI takes over) and again from
    light_console_pin after the TUI mounts (Textual may reset the title).
    """
    if not title:
        try:
            from magpie import __version__ as _v
            title = f"MagpieBridge v{_v} - 微信消息推送 & OneBot v11"
        except Exception:
            title = "MagpieBridge"
    try:
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        # 1) classic title: works on conhost AND is forwarded to WT tabs
        kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
        kernel32.SetConsoleTitleW.restype = wintypes.BOOL
        kernel32.SetConsoleTitleW(title)
        # 2) real window title on the visible host window (WT / conhost)
        hwnd = _get_wt_window()
        if hwnd:
            user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            user32.SetWindowTextW.restype = wintypes.BOOL
            user32.SetWindowTextW(hwnd, title)
    except Exception:
        pass


def setup_logging(config: AppConfig, base_dir: Path) -> None:
    log_path = base_dir / config.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Log to a file always; log to the TUI via a handler.  We avoid a
    # StreamHandler to stdout because Textual owns a full-screen terminal and
    # stray prints would garble it.  Startup lines are buffered by the TUI
    # log-handler and appear once the UI mounts.
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    # 文件日志保留完整格式（含毫秒 + 模块名，便于排查）
    file_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    # TUI 日志用精简彩色格式（无毫秒、无模块名前缀），由 TuiLogHandler 自行构造

    root = logging.getLogger()
    root.setLevel(level)
    tui_handler = TuiLogHandler()
    # 按天滚动日志：防止单个 log 文件无限增长（长期运行会越来越大，
    # 打开/写入变慢甚至占满磁盘）。保留最近 30 天，旧文件自动删除。
    # 文件名: logs/magpie.log -> 当天写主文件，次日轮转成
    # magpie.log.YYYY-MM-DD，保留 30 份。
    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_path),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setFormatter(logging.Formatter(file_fmt))
    # 中文 Windows 控制台/日志后缀: 默认 suffix 是 %Y-%m-%d，轮转后的
    # 文件名形如 magpie.log.2026-09-02，可直接用日志名识别日期。
    root.addHandler(file_handler)
    root.addHandler(tui_handler)

    # Silence noisy server access logs (e.g. "200 OK" spam)
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    # let the TUI know where the log file is (to seed the log panel on mount)
    try:
        bus.update_status(log_file=str(log_path))
    except Exception:
        pass


def sync_bus_status(monitor: StatusMonitor, engine: UIEngine, listener: WeChatListener, config: AppConfig) -> None:
    """Refresh the shared TUI status bus from the live components."""
    st = monitor.get_status()
    bus.update_status(
        status=st.get("status", "initializing"),
        uptime_seconds=int(st.get("uptime_seconds", 0)),
        messages_sent=int(st.get("messages_sent", 0)),
        errors=int(st.get("errors", 0)),
    )
    bus.update_status(
        onebot_http=f"http://{config.onebot.http_host}:{config.onebot.http_port}",
        onebot_ws=f"ws://{config.onebot.ws_host}:{config.onebot.ws_port}",
        web_url=f"http://{config.web.host}:{config.web.port}",
    )
    try:
        es = engine.get_status()
        bus.update_status(
            wechat_found=es.get("window_found", False),
            wechat_initialized=es.get("initialized", False),
            wechat_version=es.get("wechat_version", "?"),
        )
    except Exception:
        pass
    try:
        bus.update_status(listener_running=bool(listener.running))
    except Exception:
        pass


async def main() -> None:
    base_dir = get_base_dir()

    # ===== 自动提权到管理员 =====
    # tscon /dest:console（"开始挂起"）与 HKLM 防锁屏注册表都需要管理员权限；
    # 非管理员下 tscon 会报"错误5 拒绝访问"，导致会话无法挂回控制台 —— 一旦断开
    # RDP 就会锁屏、合成输入失效，这正是"断开远程桌面后发送必失败"的根因。
    # 这里在启动时检测，非管理员则用 runas 重新以管理员启动一次（会弹一次 UAC）。
    # --no-admin：开发/CI 模式，跳过提权（tscon/防锁屏会降级，发送不受影响）。
    if "--no-admin" in sys.argv:
        pass
    elif not _is_admin():
        _relaunch_as_admin()
        return

    # 单实例保护：重复启动（如双击两次）直接退出，避免抢端口/双 TUI 混乱。
    # 开发调试可用 --allow-multiple 绕过。
    if "--allow-multiple" not in sys.argv and not _acquire_single_instance():
        print("MagpieBridge 已在运行（如需多开请加 --allow-multiple 参数）", file=sys.stderr)
        return

    # DPI awareness FIRST — before any window/DPI API.  Without it the exe is
    # DPI-unaware and SetWindowPos coords get virtualized on scaled displays
    # (size AND position silently wrong, yet read-back "confirms" the target).
    _dpi_diag = _enable_dpi_awareness()

    config_path = base_dir / "config.json"
    config = AppConfig.load(config_path)
    setup_logging(config, base_dir)
    logger = logging.getLogger("magpie")
    logger.info("%s", _dpi_diag)
    logger.info("终端类型: %s", _detect_terminal())

    # 记录微信窗口矩形，供控制台窗口定位时避开微信（见 _compute_console_target）
    _set_wechat_rect(
        config.wechat.window_position_x,
        config.wechat.window_position_y,
        config.wechat.window_width,
        config.wechat.window_height,
    )

    # 控制台外观设置（按终端宿主分流，见 pin_console_to_top_right）：
    #   conhost → 字体先行（px<->字符换算基准）→ 窗口像素尺寸 → 右上角 → 禁 IME
    #   Windows Terminal → 直接 SetWindowPos 真实宿主窗口（conhost API 全无效）
    # 全部在 TUI 接管全屏之前完成，避免启动闪烁。
    pin_console_to_top_right()

    # 启动 0.8s 后校验窗口尺寸：仅在第一次设置未生效（偏差 > 40px）时补设
    # 一次；已正确则 no-op（不触发重绘，保持防闪烁）。
    loop = asyncio.get_event_loop()
    loop.call_later(0.8, _verify_console_size)

    logger.info("MagpieBridge v%s 启动", __import__("magpie").__version__)

    logger.info("程序目录: %s", base_dir)

    # 自动把会话挂回本机控制台 + 防锁屏（自动版「开始挂起」）。
    # 断开远程桌面后会话会进入 WTSDisconnected/锁屏，合成输入失效导致无法发送；
    # 启动时若会话在远程会话/已断开/锁屏则自动挂回控制台，并始终应用防锁屏，
    # 避免用户每次都得手动按 Ctrl+Alt+E。（force=False：已在控制台时不主动
    # 跑 tscon，防止对控制台会话误触发 UAC 提权弹窗。）
    try:
        from magpie.core.session_control import ensure_session_active
        _ok, _msg = ensure_session_active(force=False)
        logger.info("会话保活(启动): %s", _msg)
    except Exception:
        logger.exception("启动时会话保活异常")

    engine = UIEngine(config)
    msg_db_path = str(base_dir / "data" / "magpie.db")
    msg_logger = MessageStore(msg_db_path)
    monitor = StatusMonitor()

    # SMTP 邮件告警器（发送失败 / 风控时提醒人工接管）
    from magpie.core.alerter import EmailAlerter, set_alerter
    alerter = EmailAlerter(config.alert)
    set_alerter(alerter)
    if config.alert.enabled:
        logger.info("邮件告警已启用: %s -> %s", config.alert.smtp_user or "(未填发件箱)", config.alert.receiver or "(未填收件人)")
    else:
        logger.info("邮件告警未启用（可在后台「设置」中开启并填写授权码）")

    init_ok = await engine.initialize()
    if init_ok:
        logger.info("微信引擎初始化成功")
    else:
        logger.warning("微信引擎初始化失败，以降级模式运行")

    ob_http = OneBotHTTP(
        engine,
        host=config.onebot.http_host,
        port=config.onebot.http_port,
        token=config.onebot.access_token,
    )
    ob_ws = OneBotWebSocket(
        engine,
        host=config.onebot.ws_host,
        port=config.onebot.ws_port,
        token=config.onebot.access_token,
    )

    admin_handler = AdminCommandHandler(config.admin, engine, msg_logger)

    # Plugin system: load from Plugins/ dir, attach to engine + OneBot + listener
    from magpie.plugin.manager import PluginManager

    pm = PluginManager(engine)
    pm.load_plugins()
    engine.plugin_manager = pm
    ob_http.set_plugin_manager(pm)
    ob_ws.set_plugin_manager(pm)

    listener = WeChatListener(config.monitor, engine, plugin_manager=pm, store=msg_logger)

    app = create_app(config, engine, msg_logger, monitor, listener, pm)

    web_config = uvicorn.Config(
        app,
        host=config.web.host,
        port=config.web.port,
        log_level="warning",
    )
    web_server = uvicorn.Server(web_config)

    await ob_http.start()
    await ob_ws.start()
    await engine.op_queue.start()
    await admin_handler.start()
    if config.monitor.enabled:
        await listener.start()
        monitor.set_status("running")
    else:
        monitor.set_status("monitor_off")
        logger.info("未读监听未启用（config.monitor.enabled=false）")

    web_task = asyncio.create_task(web_server.serve())

    logger.info("所有服务已启动")
    logger.info("  OneBot HTTP 接口: http://%s:%d", config.onebot.http_host, config.onebot.http_port)
    logger.info("  OneBot WS 接口:   ws://%s:%d", config.onebot.ws_host, config.onebot.ws_port)
    logger.info("  Web 管理界面:     http://%s:%d", config.web.host, config.web.port)
    if config.admin.enabled:
        logger.info("  管理指令:         监控 %s", ", ".join(config.admin.admin_contacts))
    if config.monitor.enabled:
        logger.info("  未读监听:         已启用（间隔 %ds，持续监听 %s）",
                    config.monitor.poll_interval_sec,
                    "开" if config.monitor.continuous_monitor else "关")
    if pm.plugins:
        logger.info("  插件系统:         已加载 %d 个插件", len(pm.plugins))

    sync_bus_status(monitor, engine, listener, config)

    async def _status_loop() -> None:
        while True:
            await asyncio.sleep(2)
            try:
                sync_bus_status(monitor, engine, listener, config)
            except Exception:
                pass

    async def _console_loop() -> None:
        """Keep the console pinned top-right and the IME disabled.

        Re-asserting every few seconds guards against the console being moved /
        the IME being toggled by the user or by the terminal.  WT mode only
        re-pins the position (never the size — the user may resize manually).
        """
        while True:
            await asyncio.sleep(4)
            try:
                if _detect_terminal() == "wt":
                    _pin_wt_window(800, 520, force_size=False)
                else:
                    _move_console_to_top_right()
            except Exception:
                pass
            try:
                _disable_ime()
            except Exception:
                pass

    async def _session_loop() -> None:
        """Periodically keep the desktop session interactive (auto 开始挂起).

        After the RDP client is closed the session goes to WTSDisconnected (or
        locks), so synthetic input stops reaching WeChat and every send fails.
        This loop detects that state and automatically re-hangs the session to
        the console + re-applies anti-lock, so messages keep sending 7x24
        without a human pressing Ctrl+Alt+E.  Runs only when a broken state is
        detected (an actively-used RDP session is never interrupted).
        """
        while True:
            await asyncio.sleep(5)
            try:
                from magpie.core.session_control import ensure_session_active
                ok, msg = ensure_session_active()
                if not ok:
                    logger.warning("会话保活失败: %s", msg)
                elif msg != "会话处于活动状态，无需挂起":
                    logger.info("会话保活: %s", msg)
            except Exception:
                logger.exception("会话保活循环异常")

    status_task = asyncio.create_task(_status_loop())
    console_task = asyncio.create_task(_console_loop())
    session_task = asyncio.create_task(_session_loop())

    # ---- run the Textual TUI, with auto-restart on crash + headless fallback ----
    # TUI 仅是渲染层，崩溃不应带走后台服务。旧版 conhost（Win10）快速拖拽窗口
    # 可能触发 textual 渲染异常，这里最多重试 3 次自动拉起新实例；彻底失败才
    # 回退无界面模式（此时服务仍在运行，避免端口占用的误判）。
    hotkey_spec = getattr(config.monitor, "browse_pause_hotkey", "ctrl+alt+s")
    hotkey = GlobalHotkey(hotkey_spec, callback=bus.toggle_browse)
    hotkey.start()
    logger.info("全局快捷键：%s （开启/关闭模拟网页浏览）", hotkey_spec)

    # 开始挂起（Ctrl+Alt+E）：挂回本机控制台，断开远程桌面，保持桌面可交互
    def _on_hang() -> None:
        try:
            from magpie.core.session_control import hang_to_console
            ok, msg = hang_to_console()
            logger.info("开始挂起(Ctrl+Alt+E)：%s", msg)
            bus.record_note("开始挂起: " + msg)
        except Exception:
            logger.exception("开始挂起异常")

    hang_hotkey = GlobalHotkey("ctrl+alt+e", callback=_on_hang)
    hang_hotkey.start()

    # 延时操作（Ctrl+Alt+Y）：让出鼠标控制权 30 秒，期间发送/浏览暂停抢鼠标
    delay_hotkey = GlobalHotkey("ctrl+alt+y", callback=lambda: bus.yield_to_user(30))
    delay_hotkey.start()

    # 息屏开关（Ctrl+Alt+O）：系统级息屏 + 保黑 + 吞没物理输入（ADR-0003）。
    # 显示器关着看不见时，一切操作走快捷键。
    def _on_screen_toggle() -> None:
        try:
            from magpie.core import screen_off

            now_off = screen_off.toggle()
            bus.record_note("息屏开" if now_off else "息屏关")
            logger.info("息屏切换(Ctrl+Alt+O)：%s", "已息屏" if now_off else "已亮屏")
        except Exception:
            logger.exception("息屏切换异常")

    screen_hotkey = GlobalHotkey("ctrl+alt+o", callback=_on_screen_toggle)
    screen_hotkey.start()

    # 息屏后按物理 ESC 唤醒：只有 ESC 能退出息屏，让用户操作。
    # 平时（未息屏）按 ESC 不做任何事，不影响其它程序。
    # （息屏期间钩子层已直接处理 ESC；这里留一层兜底。）
    def _on_esc_wake() -> None:
        try:
            from magpie.core import screen_off

            if screen_off.is_screen_off():
                screen_off.wake_display(reason="esc")
                bus.record_note("已按 ESC 退出息屏")
        except Exception:
            pass

    esc_hotkey = GlobalHotkey("esc", callback=_on_esc_wake)
    esc_hotkey.start()
    logger.info("全局快捷键：ctrl+alt+o （息屏/亮屏） / esc （息屏时恢复亮屏）")

    stop_event = asyncio.Event()
    _active_tui = None

    def _signal_handler() -> None:
        logger.info("收到关闭信号")
        try:
            if _active_tui is not None:
                _active_tui.exit()
        except Exception:
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    tui_started = False
    for _attempt in range(1, 4):
        try:
            tui = MagpieTui(
                engine=engine,
                monitor=monitor,
                msg_logger=msg_logger,
                listener=listener,
                plugin_manager=pm,
                on_mount_cb=light_console_pin,
            )
            _active_tui = tui
            await tui.run_async()
            tui_started = True
            _active_tui = None
            break
        except Exception:
            logger.exception("TUI 异常退出（第 %d 次）", _attempt)
            _active_tui = None
            if _attempt < 3:
                logger.warning("2 秒后自动重启 TUI…")
                await asyncio.sleep(2)
            else:
                logger.warning(
                    "TUI 多次异常退出，已回退到无界面模式；后台服务仍在运行，"
                    "可用 Ctrl+C 正常退出（勿重复启动避免端口占用）"
                )

    if not tui_started:
        logger.info("等待 Ctrl+C 停止服务...")
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass

    logger.info("正在关闭...")
    try:
        hotkey.stop()
    except Exception:
        pass
    for _hk in (hang_hotkey, delay_hotkey, screen_hotkey, esc_hotkey):
        try:
            _hk.stop()
        except Exception:
            pass
    web_server.should_exit = True
    await admin_handler.stop()
    await listener.stop()
    await engine.op_queue.stop()
    await ob_ws.stop()
    msg_logger.close()

    # graceful uvicorn shutdown (avoids the CancelledError lifespan traceback)
    try:
        await web_server.shutdown()
    except Exception:
        pass
    finally:
        try:
            web_task.cancel()
            await web_task
        except Exception:
            pass
    for t in (status_task, console_task, session_task):
        try:
            t.cancel()
            await t
        except Exception:
            pass
    logger.info("关闭完成")


if __name__ == "__main__":
    asyncio.run(main())
