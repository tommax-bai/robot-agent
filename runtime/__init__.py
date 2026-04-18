"""运行时：DI 容器 + 执行上下文 + 事件总线。

公共入口：
- Host / Topology / Host.boot() / Host.current()
- Worker
- RunContext / CancelScope / CancelledError
- EventBus / Event / payload / attach_log_bridge
"""

from __future__ import annotations

from runtime.ctx import CancelledError, CancelScope, RunContext
from runtime.events import Event, EventBus, attach_log_bridge, payload
from runtime.host import Host, Topology
from runtime.worker import Worker

__all__ = [
    "CancelScope",
    "CancelledError",
    "Event",
    "EventBus",
    "Host",
    "RunContext",
    "Topology",
    "Worker",
    "attach_log_bridge",
    "payload",
]
