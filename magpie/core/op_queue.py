"""Serialized WeChat UI operation queue.

MagpieBridge simulates a human operating WeChat, so only ONE UI operation may
run at a time.  When several tasks arrive simultaneously (e.g. a plugin
forwards N leads, or an admin command + a plugin reply overlap), they must be
queued and executed strictly one after another.

This module provides:

- `HOLD_UI_LOCK` : a global threading.RLock guarding ALL WeChat UI interaction.
  The monitor listener and the operation queue both acquire it, so a scan and
  a send can never interleave.

- `OperationQueue`: an asyncio worker that runs submitted jobs one at a time.
  Each job is a synchronous function that drives WeChat (search + type + send).
  It is executed in a thread while holding `HOLD_UI_LOCK`.

Each queued operation is mirrored to `bus.record_op()` so the TUI bottom
"操作队列" panel shows queued -> running -> done/fail live.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Optional

from ..tui import bus

logger = logging.getLogger(__name__)

# Global lock that serialises every WeChat UI touch (scan + send).
HOLD_UI_LOCK = threading.RLock()

Job = Callable[[], Any]


class OperationQueue:
    """Process WeChat UI jobs strictly one at a time."""

    def __init__(self, maxsize: int = 32, job_timeout: float = 180.0,
                 max_queue_wait: float = 1800.0) -> None:
        self._queue: asyncio.Queue[tuple[dict, Job, "asyncio.Future[Any]"]] = asyncio.Queue(maxsize=maxsize)
        self._worker: Optional[asyncio.Task] = None
        self._running = False
        self._counter = 0
        self._maxsize = maxsize
        self._job_timeout = job_timeout
        self._max_queue_wait = max_queue_wait

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = asyncio.create_task(self._run())
        logger.info("微信操作队列已启动（串行，一次仅处理一条）")

    async def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        # 兜底：把仍排队未决的任务全部 resolve，防止 submit() 永久挂起。
        while not self._queue.empty():
            try:
                _entry, _job, fut = self._queue.get_nowait()
                if not fut.done():
                    fut.set_exception(RuntimeError("操作队列已停止"))
            except Exception:
                break

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ---------------------------------------------------------- submit
    async def submit(self, kind: str, target: str, detail: str, job: Job, timeout: Optional[float] = None) -> Any:
        """Queue one operation; await it completes. Returns the job's result.

        `job` is a sync callable that drives WeChat.  It runs in a thread under
        `HOLD_UI_LOCK`.  If it raises, the exception is re-raised to the caller.

        Guards against unbounded/never-completing submissions:
        - queue full (>= maxsize)          -> raise immediately (fail fast)
        - job exceeds `timeout` **while executing** -> raise TimeoutError.
          Queueing time does NOT eat the job timeout any more: with a serial
          queue, backlog used to turn healthy jobs into a cascade of false
          "TimeoutError 发送失败" which then triggered upstream retry storms
          (the retried batches made the backlog worse -- a feedback loop).
          Waiting in queue is capped separately by `max_queue_wait`.
        - NOTE: the underlying UI action may still complete after a job
          timeout (UI automation is not abortable), so callers must NOT
          blindly retry a timed-out send — the message may already be out.
        """
        if timeout is None:
            timeout = self._job_timeout
        if self._queue.qsize() >= self._maxsize:
            raise RuntimeError(
                f"操作队列已满({self._maxsize})，拒绝新任务: {kind} {target} {detail}"
            )
        self._counter += 1
        entry = bus.record_op(kind, target, detail, "queued")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        await self._queue.put((entry, job, fut))
        t0 = time.monotonic()
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(fut), timeout=1.0)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    started = entry.get("started_at")
                    if started is not None:
                        if now - started > timeout:
                            raise
                    elif now - t0 > self._max_queue_wait:
                        raise  # 排队预算耗尽：前面的任务卡死了
        except asyncio.TimeoutError:
            # 任务已从队列取出并可能正在执行（to_thread 无法中止）。不手动把 fut
            # 置为异常 —— caller 已拿到 TimeoutError，若再 set_exception 会触发
            # asyncio 内部 "exception in shielded future" 噪音日志；保持 pending，
            # worker 稍后完成时 set_result 被丢弃即可（已完成 future 无告警）。
            raise

    # ---------------------------------------------------------- worker
    async def _run(self) -> None:
        while self._running:
            try:
                entry, job, fut = await self._queue.get()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("操作队列取出任务失败")
                continue

            entry["status"] = "running"
            entry["started_at"] = time.monotonic()
            try:
                result = await asyncio.to_thread(self._run_locked, job)
                entry["status"] = "ok"
                if not fut.done():
                    fut.set_result(result)
            except asyncio.CancelledError:
                entry["status"] = "cancel"
                if not fut.done():
                    fut.set_exception(asyncio.CancelledError())
                    try:
                        fut.exception()  # 无 await 者时避免未检索异常告警
                    except Exception:
                        pass
                break
            except Exception as e:
                entry["status"] = "fail"
                entry["error"] = str(e)
                if not fut.done():
                    fut.set_exception(e)

    def _run_locked(self, job: Job) -> Any:
        """Run a WeChat UI job while holding the global UI lock."""
        with HOLD_UI_LOCK:
            # 用户按了 Ctrl+Alt+P（延时操作）：让出控制权，等让出期结束再执行任务
            bus.wait_yield_release()
            return job()
