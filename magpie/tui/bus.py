"""In-process event bus for the Textual TUI.

A tiny, dependency-free singleton that lets any core module (engine, listener,
admin, OneBot) publish high-level events that the TUI renders: the software /
WeChat status line, the left log, the right action/audit log and the bottom
operation queue.  Kept separate from the widgets so core code never imports
Textual.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Optional

_status: dict[str, Any] = {}
_ops: deque[dict] = deque(maxlen=300)
_notes: deque[dict] = deque(maxlen=300)
_log_buffer: deque[str] = deque(maxlen=2000)
# 已推送的全部日志行历史（只读重播用：窗口 resize 后 RichLog 需要按新宽度
# 重新 wrap，而 drain_log 取走即清空，必须保留一份副本供重写）
_log_history: deque[str] = deque(maxlen=2000)


def _ts() -> str:
    return time.strftime("%H:%M:%S")


# ---------------------------------------------------------------- status
def update_status(**kv: Any) -> None:
    _status.update(kv)


def get_status() -> dict[str, Any]:
    return dict(_status)


# ---------------------------------------------------------------- ops queue
def record_op(kind: str, target: str = "", detail: str = "", status: str = "running") -> dict:
    """Queue one operation (shown in the bottom '操作队列' panel)."""
    entry = {
        "ts": time.time(),
        "time": _ts(),
        "kind": kind,
        "target": str(target),
        "detail": str(detail),
        "status": status,
    }
    _ops.appendleft(entry)  # newest first
    return entry


def finish_op(entry: Optional[dict], status: str = "ok") -> None:
    """Mark a previously queued operation as finished."""
    if entry is not None:
        entry["status"] = status


def get_ops(limit: int = 100) -> list[dict]:
    return list(_ops)[:limit]


# ---------------------------------------------------------------- notes
def record_note(text: str) -> None:
    _notes.appendleft({"ts": time.time(), "time": _ts(), "text": text})


def get_notes(limit: int = 100) -> list[dict]:
    return list(_notes)[:limit]


# ---------------------------------------------------------------- browse toggle
# "模拟浏览网页" is OFF by default.  It can be turned on with Ctrl+Alt+S or
# from the web 管理后台 toggle.
_browse_enabled = False


def is_browse_enabled() -> bool:
    return _browse_enabled


def set_browse_enabled(enabled: bool) -> None:
    global _browse_enabled
    if _browse_enabled != enabled:
        record_note("网页浏览已" + ("开启" if enabled else "关闭"))
    _browse_enabled = enabled


def toggle_browse() -> bool:
    """Toggle the simulate-web-browsing feature. Returns the new state."""
    global _browse_enabled
    _browse_enabled = not _browse_enabled
    record_note("网页浏览已" + ("开启" if _browse_enabled else "关闭"))
    return _browse_enabled


def is_browse_paused() -> bool:
    """Backwards-compatible alias: paused == browse feature disabled."""
    return not _browse_enabled


def toggle_browse_pause() -> bool:
    """Backwards-compatible alias returning the new 'paused' state."""
    enabled = toggle_browse()
    return not enabled


# ---------------------------------------------------------------- user yield (延时操作)
# 发送/浏览任务会持续抢鼠标并反复把微信置前，用户想临时操作别的窗口时根本
# 腾不出手。Ctrl+Alt+P（"延时操作"）把"让出控制权"的标志设 30 秒：期间所有
# 鼠标动作（move/click/scroll）与"置前微信"都会等待，用户可自由操作；30 秒
# 后若程序仍在，则继续暂停前的任务。
_yield_until = 0.0


def yield_to_user(seconds: float = 30.0) -> None:
    """让出鼠标控制权给用户 seconds 秒。期间发送/浏览的鼠标操作与置前暂停。"""
    global _yield_until
    _yield_until = time.monotonic() + seconds
    record_note(f"延时操作：已让出控制权 {int(seconds)} 秒")


def is_yielding() -> bool:
    return time.monotonic() < _yield_until


def wait_yield_release() -> None:
    """若处于让出期，阻塞到结束。供会抢鼠标/置前的操作在开始时调用。"""
    while is_yielding():
        time.sleep(0.2)


# ---------------------------------------------------------------- log drain
def push_log(line: str) -> None:
    _log_buffer.append(line)
    _log_history.append(line)


def drain_log(limit: int = 200) -> list[str]:
    out: list[str] = []
    while _log_buffer and len(out) < limit:
        out.append(_log_buffer.popleft())
    return out


def get_log_history(limit: int = 600) -> list[str]:
    """Return the most recent log lines without consuming the buffer.

    Used to re-seed the RichLog panel after a resize so lines re-wrap to the
    new panel width (RichLog wraps once at write time and never re-wraps).
    """
    return list(_log_history)[-limit:]
