"""
EventBus + 事件类型 + 日志桥接。

设计要点：
- 发布非阻塞：慢订阅者丢最早事件（监控场景下新事件优先）。
- publish 可跨线程（SessionManager.release 等），订阅列表加锁。
- Event 是 StrEnum：SSE 事件名 = 枚举值；类型安全替代字符串常量。
- attach_log_bridge 把 utils.logger 的记录转发为 Event.LOG 事件，方便 dashboard SSE。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from enum import StrEnum
from typing import Any

_PER_SUBSCRIBER_QUEUE_MAX = 256


class Event(StrEnum):
    MODE_CHANGED = "mode_changed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    RUNTIME_CHANGED = "runtime_changed"
    LOG = "log"


class EventBus:
    """极简 in-process pub/sub。

    线程安全：publish 可能来自任意线程（SessionManager.release、_LogHandler.emit
    挂在 root logger），而 asyncio.Queue 本身不保证跨线程安全。所以：
    - subscribe 时捕获当前运行的事件循环，和队列一起存起来
    - publish 不直接 put_nowait，而是 loop.call_soon_threadsafe 到该队列所属 loop
    - QueueFull / QueueEmpty 只在回调内部处理，避免与 loop 内部状态竞争
    """

    def __init__(self):
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def publish(self, event: Event | str, payload: dict | None = None) -> None:
        evt_type = event.value if isinstance(event, Event) else event
        record = {"type": evt_type, "ts": time.time(), "payload": payload or {}}
        with self._lock:
            snapshot = list(self._subscribers)
        for loop, q in snapshot:
            try:
                loop.call_soon_threadsafe(_deliver, q, record)
            except RuntimeError:
                # loop 已关闭（进程退出 / 订阅者在 shutdown 未及时 unsubscribe），静默丢弃
                pass

    def subscribe(self) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=_PER_SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(l, sq) for (l, sq) in self._subscribers if sq is not q]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


def _deliver(q: asyncio.Queue, record: dict) -> None:
    """在 Queue 所属 loop 内投递。满则丢一个最旧事件给新事件让位。"""
    try:
        q.put_nowait(record)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(record)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


def payload(**kwargs: Any) -> dict:
    """过滤掉 None 字段，避免 SSE 串中出现 null。"""
    return {k: v for k, v in kwargs.items() if v is not None}


# ─── 日志桥接 ─────────────────────────────────────────────────


class _LogHandler(logging.Handler):
    def __init__(self, bus: EventBus, level: int = logging.INFO):
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fields: dict[str, Any] = {"msg": record.getMessage()}
            structured = getattr(record, "structured", None)
            if isinstance(structured, dict):
                fields.update(structured)
            tid = getattr(record, "trace_id", "") or ""
            if tid:
                fields["trace_id"] = tid
            self._bus.publish(
                Event.LOG,
                {
                    "level": record.levelname,
                    "logger": record.name,
                    "module": record.module,
                    "line": record.lineno,
                    **fields,
                },
            )
        except Exception:
            self.handleError(record)


def attach_log_bridge(bus: EventBus, target: str = "") -> _LogHandler:
    """把日志记录转发到 EventBus。默认挂 root，覆盖 facade + stdlib `getLogger(__name__)` 两种调用。"""
    handler = _LogHandler(bus)
    logging.getLogger(target).addHandler(handler)
    return handler


__all__ = ["Event", "EventBus", "attach_log_bridge", "payload"]
