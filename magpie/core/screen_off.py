"""息屏控制（ADR-0003）：系统级息屏 + 保黑看门狗 + 物理输入吞没。

设计要点（为什么是系统级息屏而不是黑色遮罩）：
- SC_MONITORPOWER 只关背光、不改显示拓扑 → 微信窗口坐标不重排，自动化照常；
- 截图走屏幕 BitBlt，背光关不影响像素内容，OCR 照常；
- 黑色遮罩窗口有 z-order 竞争风险（restore_and_focus 置前微信时会互相打架），
  不省电也不防误碰，因此只作为可选 fallback（mode="overlay"，默认不启用）。

息屏期间：
- 保黑看门狗每 6s 重新压黑一次（防 RDP 接入/杂散输入唤醒背光）；
- 低级钩子吞掉**物理**键鼠事件（键 except ESC；鼠标全部），机器完全交给宿主；
- 带 LLKHF_INJECTED 标记的**合成**事件（宿主自己的自动化输入）全部放行；
- 按 ESC / Ctrl+Alt+O 立即唤醒（钩子内部直接处理，不依赖前台焦点）。
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

logger = logging.getLogger(__name__)

WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
HWND_BROADCAST = 0xFFFF
POWER_ON = -1
POWER_OFF = 2

REBLACK_INTERVAL = 6.0  # 秒：保黑看门狗周期

VK_ESCAPE = 0x1B
VK_O = 0x4F
VK_CONTROL = 0x11
VK_MENU = 0x12

LLKHF_INJECTED = 0x10
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

_lock = threading.RLock()
_screen_off = False
_overlay_on = False
_wake_reason = ""
_stop_reblack = threading.Event()
_reblack_thread: threading.Thread | None = None
_hook_thread: threading.Thread | None = None
_hook_ready = threading.Event()
_on_wake_callbacks: list = []

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LRESULT = ctypes.c_ssize_t  # LONG_PTR：64 位下句柄/返回值不被截断
HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)
MOUSEPROC = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)

# 显式签名：64 位下 HMODULE/HOOK 返回值不声明会被截断
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, ctypes.c_void_p]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

WM_QUIT = 0x0012
_hook_ids: list[int] = []
_hook_tid: int = 0  # 钩子线程的 Win32 线程 id（卸钩消息发给它）
_kb_proc_ref: HOOKPROC | None = None
_mouse_proc_ref: MOUSEPROC | None = None
WM_APP_UNHOOK = 0x0400 + 0x517


def _monitor_power(mode: int) -> None:
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_void_p
    # HWND_BROADCAST 的 SendMessage 会**同步等待所有顶层窗口**处理完才返回——
    # 任何一个不泵消息的窗口都能把保黑看门狗/唤醒链路整个挂死。
    # SendMessageTimeoutW + SMTO_ABORTIFHUNG(0x0002)：挂起窗口直接放弃，2 秒兜底。
    user32.SendMessageTimeoutW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
        ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
    ]
    user32.SendMessageTimeoutW.restype = ctypes.c_void_p
    result = ctypes.c_void_p(0)
    user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, mode,
        0x0002, 2000, ctypes.byref(result),
    )


def _black_window_start() -> None:
    """overlay fallback：置顶纯黑窗口盖屏（默认不用，见 ADR-0003）。"""
    global _overlay_on
    if _overlay_on:
        return
    try:
        import tkinter

        root = tkinter.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-topmost", True)
        root.configure(background="black")
        root.after(200, lambda: root.focus_force())
        threading.Thread(target=root.mainloop, name="screen-overlay", daemon=True).start()
        _overlay_on = True
        logger.info("息屏(遮罩模式)：全屏黑窗已盖屏")
    except Exception as e:
        logger.warning("遮罩窗口创建失败: %s", e)


def _black_window_stop() -> None:
    global _overlay_on
    _overlay_on = False
    # tkinter 窗口随进程存活，独立 CLI 用完即退；宿主内默认走 monitor 模式


# ---------------------------------------------------------------- 输入吞没
def _is_off() -> bool:
    with _lock:
        return _screen_off


def _kbd_proc(nCode, wParam, lparam):  # noqa: N802 - Win32 回调
    try:
        if nCode == 0 and _is_off():
            info = lparam[0]
            injected = bool(info.flags & LLKHF_INJECTED)
            if not injected:
                vk = info.vkCode
                ctrl = user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                alt = user32.GetAsyncKeyState(VK_MENU) & 0x8000
                if vk == VK_ESCAPE:
                    threading.Thread(target=wake_display, kwargs={"reason": "esc"}, daemon=True).start()
                    return 1
                if vk == VK_O and ctrl and alt:
                    threading.Thread(target=wake_display, kwargs={"reason": "hotkey"}, daemon=True).start()
                    return 1
                return 1  # 吞掉其余一切物理键盘输入
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lparam)


def _mouse_proc(nCode, wParam, lparam):  # noqa: N802 - Win32 回调
    try:
        if nCode == 0 and _is_off():
            info = lparam[0]
            if not (info.flags & LLKHF_INJECTED):
                return 1  # 吞掉一切物理鼠标输入（含移动），防误碰唤醒/误点
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lparam)


def _hook_pump() -> None:
    """钩子线程：装钩 + 消息泵；收到 WM_APP_UNHOOK 卸钩退出。"""
    global _kb_proc_ref, _mouse_proc_ref, _hook_ids, _hook_tid
    _hook_tid = kernel32.GetCurrentThreadId()
    _kb_proc_ref = HOOKPROC(_kbd_proc)
    _mouse_proc_ref = MOUSEPROC(_mouse_proc)
    h_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kb_proc_ref, None, 0)
    h_ms = user32.SetWindowsHookExW(WH_MOUSE_LL, _mouse_proc_ref, None, 0)
    _hook_ids = [h for h in (h_kb, h_ms) if h]
    _hook_ready.set()
    if not _hook_ids:
        logger.warning("输入钩子安装失败（息屏期间无法吞没物理输入）")
        _hook_tid = 0
        return
    msg = wintypes.MSG()
    lpmsg = ctypes.byref(msg)
    while user32.GetMessageW(lpmsg, None, 0, 0) > 0:
        if msg.message == WM_APP_UNHOOK:
            break
    for h in _hook_ids:
        user32.UnhookWindowsHookEx(h)
    _hook_ids = []
    _hook_tid = 0


def _install_hooks() -> None:
    global _hook_thread
    if _hook_thread and _hook_thread.is_alive():
        return
    _hook_ready.clear()
    _hook_thread = threading.Thread(target=_hook_pump, name="screen-input-hook", daemon=True)
    _hook_thread.start()
    _hook_ready.wait(2.0)


def _uninstall_hooks() -> None:
    # 卸钩要在钩子线程自己的消息循环里做：投递 WM_APP_UNHOOK 让它退出泵并自行 Unhook
    if _hook_tid:
        try:
            user32.PostThreadMessageW(_hook_tid, WM_APP_UNHOOK, 0, 0)
        except Exception:
            pass


# ---------------------------------------------------------------- 保黑看门狗
def _reblack_loop() -> None:
    while not _stop_reblack.wait(REBLACK_INTERVAL):
        if not _is_off():
            break
        try:
            _monitor_power(POWER_OFF)  # RDP 接入/杂散输入可能唤醒 → 重新压黑
        except Exception:
            break


def _start_reblack() -> None:
    global _reblack_thread
    if _reblack_thread and _reblack_thread.is_alive():
        return
    _stop_reblack.clear()
    _reblack_thread = threading.Thread(target=_reblack_loop, name="screen-reblack", daemon=True)
    _reblack_thread.start()


def _stop_reblack_now() -> None:
    _stop_reblack.set()


# ---------------------------------------------------------------- 公共 API
def on_wake(callback) -> None:
    """注册息屏被唤醒时的回调（宿主用来记 TUI 日志）。"""
    _on_wake_callbacks.append(callback)


def turn_off_display(mode: str = "monitor") -> bool:
    """息屏：关背光（显示器仍在线，微信不锁），后台照常运行。

    mode="monitor"（默认）：系统级息屏 + 保黑 + 物理输入吞没；
    mode="overlay"：全屏黑窗遮罩 fallback（不吞输入、不省电）。
    """
    global _screen_off
    with _lock:
        _screen_off = True
    if mode == "overlay":
        _black_window_start()
        logger.info("已息屏（遮罩模式：全屏黑窗）")
        return True
    _install_hooks()
    _start_reblack()
    _monitor_power(POWER_OFF)
    logger.info("已息屏（系统级：背光关闭、显示器在线、物理输入已吞没；ESC / Ctrl+Alt+O 唤醒）")
    return True


def wake_display(reason: str = "") -> bool:
    """唤醒：恢复背光 + 停保黑 + 卸输入钩子。"""
    global _screen_off, _wake_reason
    with _lock:
        was_off = _screen_off
        _screen_off = False
        _wake_reason = reason
    if not was_off:
        return False
    _stop_reblack_now()
    _uninstall_hooks()
    _black_window_stop()
    _monitor_power(POWER_ON)
    logger.info("已亮屏（退出息屏，原因=%s）", reason or "手动")
    for cb in list(_on_wake_callbacks):
        try:
            cb(reason)
        except Exception:
            pass
    return True


def is_screen_off() -> bool:
    return _is_off()


def toggle() -> bool:
    """切换息屏状态，返回切换后的状态。"""
    if is_screen_off():
        wake_display(reason="toggle")
        return False
    turn_off_display()
    return True
