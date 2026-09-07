"""Global system-wide hotkey (polling) for pausing/resuming idle browsing.

During debugging the user wants the software to NOT open browser tabs or grab
focus.  A global hotkey (default Ctrl+Alt+S) toggles a "browse paused" flag;
the listener honours it and stops touching the browser.

We use a dedicated thread that polls `GetAsyncKeyState` for the modifier+key
combo.  This is far more robust than RegisterHotKey (no window class / message
loop needed) and works regardless of which app currently has focus.  Edge
detection (press once -> toggle once) prevents toggling while held.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_VK_MOD = {
    MOD_CONTROL: 0x11,   # VK_CONTROL
    MOD_ALT: 0x12,       # VK_MENU
    MOD_SHIFT: 0x10,     # VK_SHIFT
    MOD_WIN: 0x5B,       # VK_LWIN
}

_modmap = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN}
_key_vk = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "f5": 0x74, "f6": 0x75, "f12": 0x7B}
_letter_vk = {chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)}


def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
    """Parse a hotkey spec like 'ctrl+alt+s' into (modifiers, virtual_key).

    v0.4.22: 允许不带修饰键的裸键（如 "esc"），用于"息屏后按 ESC 唤醒"。
    裸键仍需是 _key_vk 里注册过的命名键（esc/f1..f12 等），避免误解析。
    """
    parts = str(spec).lower().split("+")
    mods = 0
    vk = 0
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in _modmap:
            mods |= _modmap[p]
        elif p in _key_vk:
            vk = _key_vk[p]
        elif len(p) == 1 and p in _letter_vk:
            vk = _letter_vk[p]
        else:
            logger.warning("无法解析全局快捷键: %s", spec)
            return None
    if not vk:
        logger.warning("全局快捷键缺少按键: %s", spec)
        return None
    return mods, vk


def _is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class GlobalHotkey:
    """Poll a global hotkey from a daemon thread; fire callback on press."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.spec = spec
        self.parsed = parse_hotkey(spec)
        self.callback = callback
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._pressed = False  # edge detection

    def start(self) -> bool:
        if self._started:
            return True
        if self.parsed is None:
            return False
        self._started = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name="global-hotkey")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._started = False

    def _poll(self) -> None:
        mods, vk = self.parsed
        mod_vks = [_VK_MOD[m] for m in _VK_MOD if mods & m]
        logger.info("全局快捷键监听中: %s", self.spec)
        while self._started:
            try:
                combo_down = all(_is_down(m) for m in mod_vks) and _is_down(vk)
                if combo_down and not self._pressed:
                    self._pressed = True
                    try:
                        self.callback()
                    except Exception:
                        logger.exception("全局快捷键回调异常")
                elif not combo_down:
                    self._pressed = False
            except Exception:
                logger.exception("热键轮询异常")
            time.sleep(0.12)
