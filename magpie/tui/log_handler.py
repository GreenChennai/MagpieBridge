"""Logging handler that mirrors log records into the TUI log buffer.

Attach this handler (in addition to the file handler) so every log line
produced anywhere in the app also appears in the Textual left-hand log panel.
The app drains `bus.drain_log()` on a timer.

Format for the TUI is deliberately compact and colored:

    2026-09-01 01:21:10 [INFO] OneBot WebSocket server started on 127.0.0.1:3001

(no milliseconds, no ``logger.name:`` prefix — the file handler keeps the full
format for diagnostics).  Levels are tinted: DEBUG gray / INFO blue / WARNING
amber / ERROR red / CRITICAL bright red.

[AUDIT] records are deliberately NOT mirrored here: they are shown in the
right-hand "操作日志" panel (via the audit module's in-memory log), so we
skip logger name "audit" to avoid double-displaying them in the run log.
"""

from __future__ import annotations

import logging
import time

from . import bus

# GitHub-Dark inspired level colors (Textual markup)
LEVEL_COLORS = {
    "DEBUG": "#8b949e",
    "INFO": "#58a6ff",
    "WARNING": "#d29922",
    "ERROR": "#f85149",
    "CRITICAL": "#ff7b72",
}
_TS_COLOR = "#8b949e"


def _esc(text: str) -> str:
    """Escape Textual markup brackets so log content can't be swallowed."""
    return text.replace("[", "[[").replace("]", "]]")


class TuiLogHandler(logging.Handler):
    """A logging.Handler that buffers colored, compact lines for the TUI."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name == "audit":
                # audit ops live in the 操作日志 (audit) panel, not the run log
                return
            if record.name.startswith("aiohttp"):
                # aiohttp access log（如 `"POST /debug_poll HTTP/1.1" 200 247`）只写 log 文件，
                # 不进 TUI 运行日志——扩展调试轮询每 1.5s 一次，刷屏无意义
                return
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            lvl = record.levelname
            color = LEVEL_COLORS.get(lvl, "#e6edf3")
            msg = _esc(record.getMessage())
            line = f"[{_TS_COLOR}]{ts}[/] [{color}][{lvl}][/] {msg}"
            bus.push_log(line)
        except Exception:
            # never let logging break the app
            pass
