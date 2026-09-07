"""Sound detection module - monitors WeChat audio via pycaw.

WeChat 4.x plays a notification sound whenever a NEW message arrives.  We hook
into the Windows Audio Session API (pycaw) and watch the *peak* audio of the
Weixin.exe session's meter.  A peak above a small threshold means WeChat just
played a sound -> there is a new message -> we can trigger a chat-list scan.

Design goals (v0.4.0 rewrite):
- Read the REAL metered peak (IAudioMeterInformation), not the master volume.
- Cooldown + debounce so a burst of sounds triggers one scan, not several.
- Degrade gracefully: if pycaw / the WeChat session cannot be found, the
  listener falls back to periodic polling (it already does).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AudioMeter:
    """Monitor WeChat audio output using pycaw."""

    def __init__(self) -> None:
        self._session = None
        self._meter = None
        self._initialized = False
        self._threshold: float = 0.012
        self._cooldown: float = 3.0        # seconds between sound triggers
        self._last_sound_time: float = 0.0
        # state-change fallback (used when the meter interface is unavailable)
        self._last_state: int = -1

    @property
    def initialized(self) -> bool:
        """是否成功挂到微信音频会话（listener 用它决定触发策略）。"""
        return self._initialized

    # ---------------------------------------------------------- setup
    def initialize(self) -> bool:
        """Find the WeChat audio session and grab its peak meter."""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

            sessions = AudioUtilities.GetAllSessions()
            for session in sessions:
                process = session.Process
                name = ""
                pid = 0
                if process is not None:
                    try:
                        name = process.name().lower()
                        pid = process.pid
                    except Exception:
                        name = ""
                if name in ("weixin.exe", "wechat.exe"):
                    self._session = session
                    self._last_state = session.State
                    try:
                        self._meter = session._ctl.QueryInterface(IAudioMeterInformation)
                        logger.info("找到微信音频会话: %s (PID: %d) · 已获取峰值表", name or "weixin", pid)
                    except Exception:
                        self._meter = None
                        logger.info("找到微信音频会话: %s (PID: %d) · 峰值表不可用，改用状态检测", name or "weixin", pid)
                    self._initialized = True
                    return True

            logger.warning("未找到微信音频会话（微信可能未播放过声音）")
            return False
        except ImportError:
            logger.warning("pycaw 未安装")
            return False
        except Exception as e:
            logger.warning("初始化音频检测失败: %s", e)
            return False

    def get_peak_level(self) -> float:
        """Current peak audio level of the WeChat session (0..1), or 0."""
        if not self._initialized or not self._session:
            return 0.0
        try:
            if self._meter is not None:
                value = self._meter.GetPeakValue()
                return float(value) if value is not None else 0.0
            # fallback: session became active = something is playing
            state = self._session.State
            return 0.5 if state == 0 else 0.0
        except Exception:
            return 0.0

    def is_sound_playing(self) -> bool:
        return self.get_peak_level() > self._threshold

    def set_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def set_cooldown(self, seconds: float) -> None:
        self._cooldown = seconds

    # ---------------------------------------------------------- async wait
    async def wait_for_sound(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for WeChat to play a sound.

        Returns True when a sound was detected (respects cooldown), False on
        timeout.  A negative timeout polls once.
        """
        if not self._initialized:
            return False

        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._wait_once():
                return True
            if time.time() >= deadline:
                return False
            await asyncio.sleep(0.3)

    def _wait_once(self) -> bool:
        """One synchronous peak check; True when a new sound was heard."""
        if not self._initialized or not self._session:
            return False
        try:
            level = self.get_peak_level()
            now = time.time()

            if level > self._threshold:
                if now - self._last_sound_time > self._cooldown:
                    self._last_sound_time = now
                    logger.info("检测到微信提示音 (peak=%.3f)，触发扫描", level)
                    return True
            else:
                # fallback: watch the session state flip to "active"
                state = self._session.State
                if state == 0 and self._last_state != 0 and now - self._last_sound_time > self._cooldown:
                    self._last_sound_time = now
                    self._last_state = state
                    logger.info("微信会话变为活跃，触发扫描")
                    return True
                self._last_state = state
        except Exception:
            pass
        return False
