"""Operation audit trail.

Every UI operation (move / click / scroll / type / search / send / monitor)
is recorded with its anti-detection parameters and the time gap from the
previous operation.  When WeChat triggers risk control (风控), grep the log
for "[AUDIT]" to see exactly which step caused it and which anti-detection
parameter was insufficient - then tune that parameter.

Log lines look like:
    19:53:40 [AUDIT] 鼠标移动        (580,515)            距离=305 时长=0.42 步数=20 gap=812ms
    19:53:41 [AUDIT] 点击            (583,517)            偏移=(3,2) 悬停=0.12 gap=1032ms
    19:53:43 [AUDIT] 发送文本        测试群                方式=逐字 长度=8 gap=2140ms

A buffer of the latest entries is kept in memory and exposed via the
`GET /api/audit/log` endpoint for quick inspection.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("audit")

_audit_buffer: deque[dict] = deque(maxlen=500)


def audit(operation: str, target: str = "", **params: Any) -> None:
    """Record one operation step with its anti-detection parameters.

    Args:
        operation: short action name, e.g. "鼠标移动", "点击", "滚动", "发送文本"
        target:    what it acted on (coords, contact name, direction...)
        params:    anti-detection-relevant values (distance, duration, delay...)
    """
    now = time.time()
    entry: dict[str, Any] = {
        "ts": now,
        "time": time.strftime("%H:%M:%S", time.localtime(now)),
        "operation": operation,
        "target": str(target),
        "params": {k: v for k, v in params.items() if v is not None},
    }
    if _audit_buffer:
        entry["gap_ms"] = int((now - _audit_buffer[-1]["ts"]) * 1000)
    _audit_buffer.append(entry)

    params_str = " ".join(f"{k}={v}" for k, v in entry["params"].items())
    if entry.get("gap_ms") is not None:
        params_str += f" gap={entry['gap_ms']}ms"
    logger.info("[AUDIT] %-12s %-22s %s", operation, target, params_str)


def get_audit_log(limit: int = 100) -> list[dict]:
    """Return the latest audit entries (newest first)."""
    return list(_audit_buffer)[-max(1, min(limit, 500)):][::-1]


def last_audit() -> Optional[dict]:
    """The most recent audit entry (or None)."""
    return _audit_buffer[-1] if _audit_buffer else None
