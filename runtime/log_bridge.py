"""
LogBridge: 把 Python logging 记录桥接到 EventBus，让 dashboard SSE 能实时看到日志。

只把 utils.logger.logger（即 'my_logger'）的记录推过去；阿里 SDK 等第三方
logger 不接入（避免 dashboard 被它们的 polling 噪音淹没）。

设计要点：
- 复用 utils.logger 已有的 payload 提取逻辑：record.payload（dict）才是关键内容
- 不格式化为字符串，结构化字段一并推给前端，前端可按 trace_id / level 过滤
- Handler 自己不能引发异常（否则 logging 模块会进入失败循环）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.event_bus import EventBus


class EventBusLogHandler(logging.Handler):
    """把每条 log record 转成 EventBus 的 'log' 事件。"""

    def __init__(self, bus: EventBus, level: int = logging.INFO):
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload_data = getattr(record, "payload", None)
            if isinstance(payload_data, dict):
                # 复制一份，避免下游 mutate 影响其他 handler
                fields = dict(payload_data)
            else:
                fields = {"msg": record.getMessage()}

            event_payload = {
                "level": record.levelname,
                "logger": record.name,
                **fields,
            }
            from runtime.event_bus import EVT_LOG  # 局部 import 避免启动期循环

            self._bus.publish(EVT_LOG, event_payload)
        except Exception:
            # logging.Handler 约定：emit 失败必须吞掉，不能抛
            self.handleError(record)


def attach(bus: EventBus, target_logger_name: str = "my_logger") -> EventBusLogHandler:
    """把 EventBusLogHandler 挂到指定 logger 上，返回 handler 引用便于 detach。"""
    target = logging.getLogger(target_logger_name)
    handler = EventBusLogHandler(bus)
    target.addHandler(handler)
    return handler
