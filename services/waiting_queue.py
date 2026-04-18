"""阻塞式等待外部 callback 的队列（如手机验证码）。

设计：
- 类化封装状态，便于注入与测试；模块级 `default` 单例兼容旧的函数式用法
- threading.Lock 保护并发；HTTP 回调与等待循环可能在不同协程 / 线程
- 不打印 result 内容到日志（可能含敏感信息，如验证码）
"""

from __future__ import annotations

import threading
import time
from typing import Any

import utils.logger as logger


class WaitingQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}

    def add(self, trace_id: str, task_name: str) -> None:
        with self._lock:
            self._entries[trace_id] = {
                "completed": False,
                "created_at": time.time(),
                "updated_at": None,
                "task_name": task_name,
                "result": None,
            }
        logger.info(
            {"msg": "等待队列加入", "trace_id": trace_id, "task": task_name, "queue_size": len(self._entries)}
        )

    def get(self, trace_id: str) -> dict[str, Any] | None:
        """读取条目快照；不存在返回 None。"""
        with self._lock:
            entry = self._entries.get(trace_id)
            return dict(entry) if entry else None

    def complete(self, trace_id: str, result: Any) -> dict:
        """由 callback 调用，标记 trace 已完成。"""
        with self._lock:
            if trace_id not in self._entries:
                logger.warning({"msg": "callback 收到未知 trace_id", "trace_id": trace_id})
                return {"code": 400, "msg": f"{trace_id} 不存在于等待队列中", "data": None}
            self._entries[trace_id]["result"] = result
            self._entries[trace_id]["completed"] = True
            self._entries[trace_id]["updated_at"] = time.time()
        logger.info({"msg": "等待队列回调成功", "trace_id": trace_id})
        return {"code": 200, "msg": f"{trace_id} 回调成功", "data": None}

    def remove(self, trace_id: str) -> bool:
        with self._lock:
            if trace_id not in self._entries:
                return False
            del self._entries[trace_id]
            return True


default = WaitingQueue()
