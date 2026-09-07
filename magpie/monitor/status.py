"""Status monitoring module."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class StatusMonitor:
    """Monitors and reports system status."""

    def __init__(self) -> None:
        self._start_time = time.time()
        self._message_count = 0
        self._error_count = 0
        self._last_activity: float = 0
        self._status: str = "initializing"

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time

    def record_message(self) -> None:
        self._message_count += 1
        self._last_activity = time.time()

    def record_error(self) -> None:
        self._error_count += 1

    def set_status(self, status: str) -> None:
        self._status = status
        logger.info("Status changed to: %s", status)

    def get_status(self) -> dict[str, Any]:
        return {
            "status": self._status,
            "uptime_seconds": int(self.uptime),
            "messages_sent": self._message_count,
            "errors": self._error_count,
            "last_activity": self._last_activity,
        }
