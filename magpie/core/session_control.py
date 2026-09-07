"""会话控制：把当前会话挂回本机控制台 + 防锁屏（"开始挂起"功能）。

整合自桌面"恢复微信桌面.bat"的 tscon 逻辑：
- 若当前是远程桌面会话，执行 `tscon <sid> /dest:console` 断开远程、把桌面挂到
  本机显示器/控制台，保持微信界面可交互（自动化才能继续）。
- 同时应用防锁屏配置（关屏保/待机/关屏/机器不活动锁定），避免会话因空闲锁定
  而切断合成输入。
- 需要管理员权限（tscon / HKLM 注册表），非管理员时调用方会收到失败提示。
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import threading
import time
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger(__name__)


def _current_session_id() -> int:
    """当前进程所在的 Windows 会话 ID。"""
    try:
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        sid = wintypes.DWORD(0)
        ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid))
        return int(sid.value)
    except Exception:
        logger.debug("获取会话 ID 失败，回退 1")
        return 1


def _apply_anti_lock() -> None:
    """应用防锁屏配置（尽力而为，HKLM 需管理员，失败静默）。"""
    try:
        for args in (
            ["powercfg", "/change", "standby-timeout-ac", "0"],
            ["powercfg", "/change", "hibernate-timeout-ac", "0"],
            ["powercfg", "/change", "monitor-timeout-ac", "0"],
        ):
            try:
                subprocess.run(args, capture_output=True, timeout=10)
            except Exception:
                pass
    except Exception:
        pass
    import os
    reg_add = r'reg.exe add'
    cmds = [
        ['reg', 'add', r'HKCU\Control Panel\Desktop', '/v', 'ScreenSaveActive', '/t', 'REG_SZ', '/d', '0', '/f'],
        ['reg', 'add', r'HKCU\Control Panel\Desktop', '/v', 'ScreenSaveTimeOut', '/t', 'REG_SZ', '/d', '0', '/f'],
        ['reg', 'add', r'HKCU\Control Panel\Desktop', '/v', 'ScreenSaverIsSecure', '/t', 'REG_SZ', '/d', '0', '/f'],
        ['reg', 'add', r'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System',
         '/v', 'InactivityTimeoutSecs', '/t', 'REG_DWORD', '/d', '0', '/f'],
    ]
    for c in cmds:
        try:
            subprocess.run(c, capture_output=True, timeout=10)
        except Exception:
            pass


def turn_off_display(mode: str = "monitor") -> bool:
    """息屏（v5.0 起委托给 magpie.core.screen_off，见 ADR-0003）。

    旧实现缺 `global _screen_off` 声明，状态永远 False，导致保黑线程秒退、
    ESC 唤醒失效；新模块同时补上了物理输入吞没与 Ctrl+Alt+O 快捷键。
    """
    from magpie.core import screen_off

    return screen_off.turn_off_display(mode=mode)


def wake_display(reason: str = "") -> bool:
    """退出息屏（委托 screen_off）。"""
    from magpie.core import screen_off

    return screen_off.wake_display(reason=reason)


def is_screen_off() -> bool:
    """当前是否处于息屏状态。"""
    from magpie.core import screen_off

    return screen_off.is_screen_off()


def hang_to_console() -> tuple[bool, str]:
    """把当前会话挂回本机控制台（断开远程桌面），并应用防锁屏配置。

    v0.4.21：挂起的同时执行「息屏」——用 SC_MONITORPOWER 关闭物理显示器
    背光（外观全黑）但保持显示器在线，避免显示器被 Windows 判为消失导致
    微信界面锁死；挂回控制台后会话保持活跃，RDP 断开也不影响发送。

    Returns:
        (ok, message)。ok=False 时 message 给出失败原因（通常是非管理员）。
    """
    sid = _current_session_id()
    _apply_anti_lock()
    # 先息屏（背光关、显示器仍在线），再挂回控制台
    turn_off_display()

    # 已挂在控制台上时，tscon 会报"会话已存在"，视为成功
    try:
        r = subprocess.run(
            ["tscon", str(sid), "/dest:console"],
            capture_output=True, text=True, timeout=20,
        )
        err = (r.stderr or r.stdout or "").strip()
        if r.returncode == 0:
            return True, f"已挂回本机控制台（会话 {sid}），远程桌面已断开，微信界面保持可交互；已息屏"
        # tscon 常见于"会话已连接/已存在"时返回非 0 —— 若会话本就在控制台，视为成功
        if ("已" in err and ("存在" in err or "连接" in err)) or "already" in err.lower():
            return True, f"会话 {sid} 已在本机控制台，无需断开；已息屏"
        # 权限不足（错误 5）：请求提权执行（弹 UAC，用户点"是"后以管理员运行 tscon）
        # 注意：不要用裸 "5" 去匹配 —— 任意含数字 5 的错误信息都会误触发 UAC，
        # 这里只认「拒绝访问 / error 5 / 错误5」这类明确权限不足的特征。
        if "拒绝访问" in err or "error 5" in err.lower() or "错误5" in err or "错误 5" in err:
            return _elevated_tscon(sid)
        return False, f"tscon 失败(退出码 {r.returncode})：{err}"
    except FileNotFoundError:
        return False, "找不到 tscon（非 Windows/无会话组件）"
    except Exception as e:
        return False, f"tscon 异常：{e}"


def _elevated_tscon(sid: int) -> tuple[bool, str]:
    """以管理员身份重新执行 tscon（弹 UAC，用户确认后生效）。"""
    import ctypes
    from ctypes import wintypes
    try:
        # 用 powershell 提权执行，让用户看到 UAC 弹窗确认
        cmd = (
            f'Start-Process -FilePath "tscon" '
            f'-ArgumentList "{sid}","/dest:console" -Verb RunAs -Wait'
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            return True, f"已通过提权挂回本机控制台（会话 {sid}），远程桌面已断开"
        return False, f"提权执行 tscon 未成功：{r.stderr.strip() or r.stdout.strip()}"
    except Exception as e:
        return False, f"提权 tscon 异常：{e}"


# ---------------------------------------------------------------------------
# 会话状态检测 + 自动保活（把手动 Ctrl+Alt+E「开始挂起」变成自动执行）
# ---------------------------------------------------------------------------
# WTS_CONNECTSTATE_CLASS 枚举值（WTSQuerySessionInformation / WTSInfoClass=8）
WTS_ACTIVE = 0        # 用户正在使用该会话（RDP 已连接且活跃）
WTS_CONNECTED = 1     # 已连接但非前台
WTS_DISCONNECTED = 4  # 远程会话已断开（关闭 RDP 客户端）
WTS_IDLE = 5          # 空闲
DESKTOP_SWITCHDESKTOP = 0x0100


def _session_state() -> Optional[int]:
    """当前会话的 WTS 连接状态（WTS_CONNECTSTATE_CLASS），读不到返回 None。"""
    try:
        wtsapi = ctypes.windll.wtsapi32
        sid = _current_session_id()
        buf = ctypes.c_void_p()
        size = wintypes.DWORD(0)
        wtsapi.WTSQuerySessionInformationW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi.WTSQuerySessionInformationW.restype = ctypes.c_int
        wtsapi.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wtsapi.WTSFreeMemory.restype = None
        if not wtsapi.WTSQuerySessionInformationW(None, sid, 8, ctypes.byref(buf), ctypes.byref(size)):
            return None
        try:
            return ctypes.cast(buf, ctypes.POINTER(ctypes.c_int))[0]
        finally:
            wtsapi.WTSFreeMemory(buf.value)
    except Exception:
        return None


def _is_session_disconnected() -> bool:
    """当前会话是否处于「已断开/空闲」状态。

    WTSDisconnected(4) 是关闭 RDP 客户端后的典型状态 —— 该状态下合成输入
    （SendInput/mouse_event）不会投递到微信界面，是「断开远程桌面后无法发送」
    的根因。WTSIdle(5) 说明会话在但无用户交互，合成输入通常仍有效，不触发挂起。
    """
    st = _session_state()
    return st == WTS_DISCONNECTED


def _input_desktop_accessible() -> bool:
    """当前线程能否访问交互式输入桌面（未锁屏）。

    OpenInputDesktop 请求 DESKTOP_SWITCHDESKTOP：会话锁屏/停在安全桌面时返回
    NULL 且 GetLastError=ERROR_ACCESS_DENIED(5)，据此判断会话已不可交互。
    """
    try:
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        h = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not h:
            return False
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop(h)
        return True
    except Exception:
        return False


def ensure_session_active(force: bool = False) -> tuple[bool, str]:
    """自动保持桌面会话可交互（自动版「开始挂起」）。

    7×24 跑机场景：用远程桌面登录并关闭客户端后，会话进入 WTSDisconnected
    或被锁屏，桌面不再是输入目标，每次注入的点击/按键都被吞掉 —— 这就是
    「关闭远程桌面后无法发送、打开前台就能发」的根因。此函数：

    1. 始终应用防锁屏配置（关屏保/待机/关屏/机器不活动锁定）；
    2. **仅在会话已断开（手动关闭远程桌面）或输入桌面不可访问（锁屏）时**，
       执行 `tscon <sid> /dest:console` 把会话挂回本机控制台，让合成输入恢复。

    ⚠️ 关键：**绝不在会话仍处于「连接/活跃」状态时挂起**。之前的实现用
    `not _is_current_session_console()` 判断 —— 用户一通过远程桌面连进来，
    会话不是控制台会话，立刻被 tscon 挂走、把用户踢下线（"我进来只能拼手速
    关掉"）。现在只在 **断开/锁屏** 时挂起：手动关闭远程桌面 → 保活接管；
    重新连进来 → 会话变活跃 → 保活自动暂停，直到再次手动关闭远程桌面。

    `force=True` 无论当前状态都尝试挂回控制台（供需要强制挂起的场景）；否则
    仅在检测到断开/锁屏等不可交互状态时才挂起。启动时用 force=False。

    Returns:
        (ok, message)
    """
    _apply_anti_lock()

    need_hang = force or _is_session_disconnected() or not _input_desktop_accessible()

    if not need_hang:
        return True, "会话处于活动状态，无需挂起"

    ok, msg = hang_to_console()
    if ok:
        logger.info("会话保活：已自动挂回本机控制台（%s）", msg)
    return ok, msg


def ensure_sendable_session(timeout: float = 30.0) -> bool:
    """发送前确保会话可交互（合成输入能到达微信）。

    远程桌面断开后会话进入 WTSDisconnected/锁屏，合成输入失效 —— 这就是
    「关闭远程桌面后发送必失败、必须在前台盯着才能发」的根因。此函数在
    发送前快速检查：

    * 会话处于连接/活跃状态（用户在远程桌面前台）→ 什么都不做，返回 True，
      绝不把用户踢下线；
    * 会话已断开/锁屏（用户关闭了远程桌面）→ 自动挂回本机控制台，并等待
      会话重新激活、桌面重新渲染后再返回 True。

    把它接到每次发送的 `_focus_wechat()` 前，即使发送正好发生在关闭远程桌面
    之后（保活 10s 循环还没轮到），也会先恢复会话再发送，从而消除失败窗口。

    Returns:
        True  = 会话可交互，可以安全发送。
        False = 会话不可交互且恢复失败，调用方应放弃发送。
    """
    if not _is_session_disconnected() and _input_desktop_accessible():
        return True

    _apply_anti_lock()
    ok, msg = hang_to_console()
    if not ok:
        logger.warning("发送前会话保活失败：%s", msg)
        return False
    logger.info("发送前会话保活：%s", msg)

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _input_desktop_accessible() and not _is_session_disconnected():
            # 挂回控制台后桌面/微信需要时间重新渲染，再等 1 秒让截图/OCR 稳定
            time.sleep(1.0)
            return True
        time.sleep(0.5)
    logger.warning("发送前会话保活：等待会话重新激活超时")
    return False
