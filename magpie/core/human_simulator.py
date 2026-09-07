"""Human behavior simulation - mouse tremor, delays, cursor trails, idle browsing."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import math
import random
import time
from typing import Optional

from .audit import audit
from .config import HumanSimConfig
from ..tui import bus

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32


# --- SendInput structs (not provided by ctypes.wintypes) ---
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


def _get_cursor_pos() -> tuple[int, int]:
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)


def _bezier_point(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """Cubic bezier interpolation."""
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


def _generate_bezier_path(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int = 20,
) -> list[tuple[int, int]]:
    """Generate a natural-looking bezier curve path between two points."""
    sx, sy = start
    ex, ey = end
    dist = math.hypot(ex - sx, ey - sy)
    curvature = random.uniform(0.2, 0.5) * (1 if random.random() > 0.5 else -1)

    cp1x = sx + (ex - sx) / 3 + random.randint(-20, 20)
    cp1y = sy + (ey - sy) / 3 + int(dist * curvature)
    cp2x = sx + 2 * (ex - sx) / 3 + random.randint(-20, 20)
    cp2y = sy + 2 * (ey - sy) / 3 - int(dist * curvature)

    path = []
    for i in range(steps + 1):
        t = i / steps
        x = int(_bezier_point(t, sx, cp1x, cp2x, ex))
        y = int(_bezier_point(t, sy, cp1y, cp2y, ey))
        path.append((x, y))
    return path


class HumanSimulator:
    """Simulates human-like mouse and keyboard behavior.

    Optionally takes a real behavior `profile` (from tools/human_behavior_recorder)
    so the simulated speed / tremor / pauses / dwell reflect a real human's
    measured actions instead of hard-coded guesses.
    """

    def __init__(self, config: HumanSimConfig | None = None, profile: dict | None = None) -> None:
        self.config = config or HumanSimConfig()
        self.profile = profile or {}
        self._tremor_phase = random.uniform(0, math.tau)

    def load_profile(self, path) -> bool:
        """Load a recorded behavior profile (JSON) from `path`."""
        if not path:
            return False
        try:
            import json
            from pathlib import Path
            p = Path(path)
            if p.exists():
                self.profile = json.loads(p.read_text(encoding="utf-8"))
                return True
        except Exception:
            pass
        return False

    # measured real-human overrides (fall back to config)
    def _profile(self, key, default, factor=1.0):
        v = self.profile.get(key) if isinstance(self.profile, dict) else None
        if v is None:
            return default
        try:
            return float(v) * factor
        except Exception:
            return default

    def random_delay(self) -> float:
        """Normal-distribution delay clamped to [min_delay_ms, max_delay_ms].

        Uniform random delays have a fixed rhythm that behavior-fingerprint
        detection can spot; a normal distribution with mean in the middle and
        sigma = range/6 keeps ~99.7% of samples inside the bounds while making
        the intervals look like a real human's.
        """
        lo, hi = self.config.min_delay_ms, self.config.max_delay_ms
        mean = (lo + hi) / 2
        std = max((hi - lo) / 6.0, 1.0)
        while True:
            ms = random.gauss(mean, std)
            if lo <= ms <= hi:
                break
        return ms / 1000.0

    def think_pause(self) -> None:
        """Normal-distribution pause before typing, mimicking human thinking."""
        mean = float(self.config.think_delay_ms)
        std = max(float(self.config.think_delay_std_ms), 1.0)
        while True:
            ms = random.gauss(mean, std)
            if 200 <= ms <= mean + 3 * std:
                break
        time.sleep(ms / 1000.0)

    def sync_delay(self) -> None:
        """Synchronous random delay."""
        time.sleep(self.random_delay())

    def apply_tremor(self, x: int, y: int) -> tuple[int, int]:
        """Apply Parkinson-like tremor to coordinates."""
        amp = self._profile("avg_tremor_px", self.config.tremor_amplitude_px)
        tx = x + random.uniform(-amp, amp)
        ty = y + random.uniform(-amp, amp)
        return int(tx), int(ty)

    def _tremor_offset(self, i: int, n: int, amp_k: float = 1.0) -> tuple[float, float]:
        """Smooth sinusoidal tremor along the path.

        Unlike per-step random jitter (which teleports the cursor and snaps
        back to the path), this produces a continuous, wave-like wobble so the
        pointer glides along a gently curved line. amp_k scales the amplitude:
        high-speed mid-flight uses less tremor, low-speed aiming uses more
        (matches real hand behavior).
        """
        amp = self._profile("avg_tremor_px", self.config.tremor_amplitude_px) * amp_k
        t = i / max(n, 1)
        phase = self._tremor_phase + t * math.tau * random.uniform(1.5, 2.5)
        ox = amp * 0.7 * math.sin(phase)
        oy = amp * 0.7 * math.sin(phase * 0.63 + 1.1)
        return ox, oy

    def _velocity_factor(self, t: float) -> float:
        """Per-step delay factor modeling a human velocity profile.

        Inspired by real mouse-trajectory data (e.g. SN SDK trajectory samples):
        - accel phase  (t < 0.12): starts hesitant, then speeds up
        - cruise phase (t < 0.85): irregular speed, delays fluctuate
        - decel phase  (t >= 0.85): slows down sharply for aiming
        """
        if t < 0.12:
            return 1.6 - 1.0 * (t / 0.12)
        if t < 0.85:
            return random.uniform(0.5, 1.5)
        return 1.5 + 2.5 * ((t - 0.85) / 0.15)

    def _aim_and_settle(self, target_x: int, target_y: int, dist: float) -> None:
        """Aiming pause + settle micro-adjustments near the target.

        Real trajectories show a ~300ms pause right before the target and a
        few 1-3px corrections around it before stabilizing.
        """
        if dist < 40:
            return
        time.sleep(random.uniform(0.15, 0.35))  # aiming pause
        cx, cy = _get_cursor_pos()
        for _ in range(random.randint(2, 4)):
            cx += random.randint(-2, 2)
            cy += random.randint(-2, 2)
            _set_cursor_pos(cx, cy)
            time.sleep(random.uniform(0.02, 0.06))
        _set_cursor_pos(target_x, target_y)

    def move_mouse_to(self, target_x: int, target_y: int, duration: Optional[float] = None) -> None:
        """Move mouse along a bezier curve with a human-like velocity profile.

        Pipeline: start micro-adjust -> accel -> cruise (irregular speed) ->
        decel -> aiming pause -> settle micro-adjustments. The tremor is
        sinusoidal and scaled by speed (less mid-flight, more when aiming).
        """
        start = _get_cursor_pos()
        # 用户按了 Ctrl+Alt+P（延时操作）：让出鼠标控制权，等让出期结束再动
        bus.wait_yield_release()
        dist = math.hypot(target_x - start[0], target_y - start[1])
        if duration is None:
            duration = self._move_duration(dist)

        # Start micro-adjustment (prepare movement) for long moves
        if dist > 80:
            _set_cursor_pos(start[0] + random.randint(-2, 2), start[1] + random.randint(-2, 2))
            time.sleep(random.uniform(0.06, 0.18))
            start = _get_cursor_pos()

        path = _generate_bezier_path(start, (target_x, target_y), self.config.bezier_steps)
        n = max(len(path) - 1, 1)
        base_step = duration / n
        self._tremor_phase = random.uniform(0, math.tau)

        for i, (px, py) in enumerate(path):
            t = i / n
            k = self._velocity_factor(t)
            amp_k = 0.4 if t < 0.85 else 1.6
            ox, oy = self._tremor_offset(i, n, amp_k)
            _set_cursor_pos(int(px + ox), int(py + oy))
            time.sleep(max(base_step * k * random.uniform(0.6, 1.4), 0.004))
            # rare, VERY brief hesitation (no stop-and-go): keeps the move smooth
            if 0.2 < t < 0.85 and random.random() < 0.04:
                time.sleep(random.uniform(0.02, 0.06))

        self._aim_and_settle(target_x, target_y, dist)
        audit("鼠标移动", f"({target_x},{target_y})",
              距离=int(dist), 时长=round(duration, 2), 步数=n)

    def wander_region(self, region, seconds: Optional[float] = None, vertical_bias: float = 0.7) -> None:
        """Bold human-like drifting inside a region (e.g. the WeChat chat list).

        The cursor wanders with genuine bezier curves and random pauses, biased
        toward VERTICAL movement (like a human scanning the list up/down), and
        every point stays inside the region so it never leaves the list area or
        interrupts the scroll.  Each hop uses the smoothed move_mouse_to, so it
        never looks like a teleport.

        Args:
            region: (x1, y1, x2, y2) screen coords of the area to stay inside.
            seconds: how long to wander (default random 0.7-1.8s).
            vertical_bias: probability a hop is a vertical scan move.
        """
        x1, y1, x2, y2 = region
        if seconds is None:
            seconds = random.uniform(0.7, 1.8)
        margin = 6
        rx1, ry1, rx2, ry2 = x1 + margin, y1 + margin, x2 - margin, y2 - margin
        cxm, cym = (x1 + x2) // 2, (y1 + y2) // 2
        end = time.time() + seconds
        hops = max(2, int(seconds / 0.32))

        for _ in range(hops):
            if random.random() < 0.10:
                # a brief pause between hops (human hesitation) - not stop-and-go
                time.sleep(random.uniform(0.04, 0.12))
                if time.time() >= end:
                    break
            if random.random() < vertical_bias:
                # vertical scan: X strays boldly, Y hops across the full list
                nx = cxm + random.randint(-60, 60)
                ny = random.randint(ry1, ry2)
            else:
                # bold both-axes drift (cover a wide swathe of the list)
                nx = random.randint(rx1, rx2)
                ny = random.randint(ry1, ry2)
            self.move_mouse_to(nx, ny)
            time.sleep(random.uniform(0.02, 0.08))
        audit("列表游走", f"区域({x1},{y1},{x2},{y2})", 秒数=round(seconds, 2))

    def randomize_target(self, x: int, y: int, radius: Optional[int] = None) -> tuple[int, int]:
        """Randomize click target within a radius (anti-detection: randomized position).

        Instead of always clicking the exact same pixel, pick a spot inside a
        small circle around the target - looks more human.
        """
        r = self.config.click_radius_px if radius is None else radius
        return x + random.randint(-r, r), y + random.randint(-r, r)

    def _move_duration(self, dist: float) -> float:
        """Human-like move duration (seconds) scaled by distance (anti-detection).

        If a recorded human profile is present we use the measured CRUISE speed
        (peak_speed_px_s) as the reference: the velocity profile then produces a
        realistic slow approach (~the measured avg_speed_px_s).  A floor keeps a
        tiny move from teleporting.
        """
        d = max(dist, 1.0)
        cruise = self._profile("peak_speed_px_s", None) or self._profile("avg_speed_px_s", None)
        if cruise:
            return max(d / cruise, 0.28) * random.uniform(0.9, 1.2)
        frac = min(d / 900.0, 1.0)
        dur_ms = self.config.move_short_ms + (self.config.move_long_ms - self.config.move_short_ms) * frac
        dur_ms = max(dur_ms, 320.0)
        return dur_ms / 1000.0 * random.uniform(0.85, 1.28)

    def click_at(self, x: int, y: int, button: str = "left") -> None:
        """Move to a randomized spot near (x, y), hesitate, then click.

        Combines all three anti-detection techniques:
        randomized target, human-like move speed, pause before clicking.
        """
        tx, ty = self.randomize_target(x, y)
        bus.wait_yield_release()   # 延时操作：让出控制权期间不点击
        self.move_mouse_to(tx, ty)
        dwell = self._profile("click_dwell_ms", self.config.hover_pause_ms)
        hover = random.uniform(0.05, max(0.05, dwell / 1000.0))
        time.sleep(hover)

        # Correct mouse button flags: LEFTDOWN=0x02 LEFTUP=0x04 RIGHTDOWN=0x08 RIGHTUP=0x10.
        # The previous code sent a DOWN with flags=0 (no button) for left clicks,
        # so WeChat never received a real click and chats did not open.
        down = 0x0008 if button == "right" else 0x0002
        up = 0x0010 if button == "right" else 0x0004
        user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(random.uniform(0.03, 0.08))
        user32.mouse_event(up, 0, 0, 0, 0)
        audit("点击", f"({x},{y})", 按钮=button, 偏移=(tx - x, ty - y), 悬停=round(hover, 2))

    @staticmethod
    def _send_unicode_char(code: int) -> None:
        """Send a single character via SendInput KEYEVENTF_UNICODE (works for CJK)."""
        ki_down = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
        ki_up = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        inp = (_INPUT * 2)()
        inp[0].type = 1  # INPUT_KEYBOARD
        inp[0].ki = ki_down
        inp[1].type = 1
        inp[1].ki = ki_up
        user32.SendInput(2, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    @staticmethod
    def _utf16_units(ch: str) -> list[int]:
        """Split a character into the UTF-16 code units SendInput expects.

        wScan is a WORD (0..0xFFFF), so astral-plane characters (emoji like
        📱/💬) must be injected as their HIGH+LOW SURROGATE pair, one
        KEYEVENTF_UNICODE event each.  Passing ord('📱')=128291 directly used
        to be silently truncated by ctypes to a garbage private-use code unit
        and the emoji simply never appeared.
        """
        cp = ord(ch)
        if cp > 0xFFFF:
            v = cp - 0x10000
            return [0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF)]
        return [cp]

    def press_newline(self) -> None:
        """Insert a line break in the WeChat input box (Ctrl+Enter).

        WeChat sends on plain ENTER, so a bare '\\n'/'\\r' keystroke would either
        be swallowed (WM_CHAR 0x0A is not a line break for that control) or —
        worse if ever remapped — send the message mid-text.  With the default
        "Enter 发送" setting, Ctrl+Enter is WeChat's line-break combo.
        """
        self.press_key_with_modifier(0x11, 0x0D)  # CTRL + ENTER
        time.sleep(random.uniform(0.03, 0.08))

    @staticmethod
    def _send_enter() -> None:
        """Send an Enter key press (confirm @ member pick, send, etc.)."""
        vk = 0x0D  # VK_RETURN
        ki_down = _KEYBDINPUT(vk, 0, 0, 0, None)
        ki_up = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
        inp = (_INPUT * 2)()
        inp[0].type = 1
        inp[0].ki = ki_down
        inp[1].type = 1
        inp[1].ki = ki_up
        user32.SendInput(2, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    @staticmethod
    def send_vk(vk: int) -> None:
        """Send a single virtual-key press (down + up) to the foreground window.

        `vk` is a Win32 virtual-key code (e.g. 0x12 == F5).  Used to scroll /
        refresh the focused browser during idle browsing.
        """
        ki_down = _KEYBDINPUT(vk, 0, 0, 0, None)
        ki_up = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
        inp = (_INPUT * 2)()
        inp[0].type = 1
        inp[0].ki = ki_down
        inp[1].type = 1
        inp[1].ki = ki_up
        user32.SendInput(2, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    @staticmethod
    def press_key_with_modifier(mod_vk: int, vk: int) -> None:
        """Send a key while holding a modifier (e.g. Shift+2 to type '@')."""
        ki = [
            _KEYBDINPUT(mod_vk, 0, 0, 0, None),
            _KEYBDINPUT(vk, 0, 0, 0, None),
            _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None),
            _KEYBDINPUT(mod_vk, 0, KEYEVENTF_KEYUP, 0, None),
        ]
        inp = (_INPUT * 4)()
        for i, k in enumerate(ki):
            inp[i].type = 1
            inp[i].ki = k
        user32.SendInput(4, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def page_scroll(self, direction: str = "down", count: int = 1) -> None:
        """Scroll the foreground window a page at a time (PgDn / PgUp).

        Used during idle browsing so the mouse stays put (doesn't steal the
        user's cursor) while the browser is scrolled realistically.
        """
        vk = 0x22 if direction == "down" else 0x21  # VK_NEXT / VK_PRIOR
        for _ in range(max(1, count)):
            self.send_vk(vk)
            time.sleep(random.uniform(0.15, 0.45))
        audit("浏览滚动", direction, 次数=count)

    def refresh_page(self) -> None:
        """Press F5 to reload the foreground browser page."""
        self.send_vk(0x74)  # VK_F5
        time.sleep(random.uniform(0.3, 0.6))
        audit("浏览刷新", "F5")

    @staticmethod
    def _send_backspace() -> None:
        """Send a Backspace key press (delete last char, human typo correction)."""
        vk = 0x08  # VK_BACK
        ki_down = _KEYBDINPUT(vk, 0, 0, 0, None)
        ki_up = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)
        inp = (_INPUT * 2)()
        inp[0].type = 1
        inp[0].ki = ki_down
        inp[1].type = 1
        inp[1].ki = ki_up
        user32.SendInput(2, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def type_text_natural(self, text: str) -> None:
        """Type text one character at a time, mimicking IME input (short texts).

        Each character arrives individually with a small random delay, a longer
        pause every 8-15 chars, and occasionally (3%) a typo that is deleted and
        re-typed - a "human imperfection" that defeats rhythm fingerprinting.
        Use for texts <= 50 chars; longer texts should be pasted instead.
        """
        step = self.config.keystroke_delay_ms / 1000.0
        typo_chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        for i, ch in enumerate(text):
            if ch == "\n":
                self.press_newline()
            else:
                for unit in self._utf16_units(ch):
                    self._send_unicode_char(unit)
            if i and i % random.randint(8, 15) == 0:
                time.sleep(random.uniform(0.25, 0.7))
            else:
                time.sleep(step * random.uniform(0.6, 1.5))

            # Human imperfection: 3% chance to type a wrong char, then fix it
            if random.random() < 0.03 and i < len(text) - 1:
                self._send_unicode_char(ord(random.choice(typo_chars)))
                time.sleep(step * random.uniform(0.8, 1.6))
                self._send_backspace()
                time.sleep(step * random.uniform(0.8, 1.6))
        audit("逐字输入", target=f"{len(text)}字符", 速度=step)

    def scroll_list(self, x: int, y: int, direction: str = "down", amount: int = 0) -> None:
        """Simulate scrolling a list with visible pointer movement.

        The cursor drifts toward the bottom-right while scrolling (like a real
        hand), with a decent drift amplitude, and moves away after finishing -
        the pointer never sits frozen on one pixel.
        """
        self.move_mouse_to(x, y)
        time.sleep(random.uniform(0.1, 0.3))
        if amount == 0:
            amount = random.randint(3, 8)
        delta = -120 if direction == "down" else 120
        drift_x, drift_y = x, y
        for _ in range(amount):
            user32.mouse_event(0x0800, 0, 0, delta, 0)
            # Drift right-down while scrolling, with a visible amplitude
            if random.random() < 0.6:
                drift_x += random.randint(0, 4)
                if direction == "down":
                    drift_y += random.randint(1, 5)
                else:
                    drift_y += random.randint(-5, -1)
                user32.SetCursorPos(drift_x, drift_y)
            time.sleep(random.uniform(0.03, 0.08))
        # Move away toward the bottom-right after finishing (operation complete)
        self.move_mouse_to(drift_x + random.randint(8, 18), drift_y + random.randint(12, 24))
        audit("滚动", direction, 次数=amount, 漂移=(drift_x - x, drift_y - y))

