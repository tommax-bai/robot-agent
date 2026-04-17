"""
EventBus: 进程内异步事件总线，用于把核心组件的状态变化推送给监控订阅者（dashboard SSE）。

设计原则：
- 发布是非阻塞的（asyncio.Queue.put_nowait）；订阅者跟不上就丢最早事件。
  原因：事件总线只服务监控/可视化，不能因为前端断网阻塞 supervisor。
- 订阅者拿到的是 dict 事件（{type, ts, payload}），自行序列化。
- 进程内单实例，由 AppContainer 注入；不打算跨进程，需要再接消息队列。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

# 单订阅者 queue 上限：超出后丢最早事件。日志事件频率比状态事件高一个数量级，
# 给到 256 大约能扛住 5-10 秒的中等突发，单订阅者内存占用约 ~32KB。
_PER_SUBSCRIBER_QUEUE_MAX = 256


class EventBus:
    """极简 in-process pub/sub。线程安全保证：subscribe/publish 都假定在 asyncio loop 内调用。"""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    def publish(self, event_type: str, payload: dict | None = None) -> None:
        """同步发布事件，永不阻塞。慢订阅者会丢掉最早事件。"""
        event = {
            "type": event_type,
            "ts": time.time(),
            "payload": payload or {},
        }
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 丢最早一个再塞，保留最新事件（监控场景下新事件比旧事件更有价值）
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self) -> asyncio.Queue:
        """返回一个新的事件 queue。订阅者用完后必须调 unsubscribe 释放。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=_PER_SUBSCRIBER_QUEUE_MAX)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# 事件类型常量（避免散落字符串拼写错误）
EVT_MODE_CHANGED = "mode_changed"
EVT_TASK_STARTED = "task_started"
EVT_TASK_COMPLETED = "task_completed"
EVT_LOG = "log"
EVT_RUNTIME_CHANGED = "runtime_changed"


def make_event_payload(**kwargs: Any) -> dict:
    """工具方法：过滤掉 None 字段，避免 SSE 串中出现 null"""
    return {k: v for k, v in kwargs.items() if v is not None}
