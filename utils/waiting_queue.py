"""
等待队列：用于阻塞式等待外部 callback（如手机验证码）。

设计原则：
- threading.Lock 保护并发访问（callback 端点和等待循环可能在不同协程/线程）
- get/remove 不存在时返回 None，调用方无需 try/except
- 不打印 result 内容到日志（可能含敏感信息如验证码）
"""
from __future__ import annotations

import threading
import time
from typing import Any

import utils.logger as logger

_lock = threading.Lock()
_queue: dict[str, dict[str, Any]] = {}


def add_queue(trace_id: str, task_name: str) -> None:
    with _lock:
        _queue[trace_id] = {
            "completed": False,
            "created_at": time.time(),
            "updated_at": None,
            "task_name": task_name,
            "result": None,
        }
    logger.info({"msg": "等待队列加入", "trace_id": trace_id, "task": task_name, "queue_size": len(_queue)})


def get_queue(trace_id: str) -> dict[str, Any] | None:
    """读取队列条目；不存在返回 None，不抛异常"""
    with _lock:
        entry = _queue.get(trace_id)
        return dict(entry) if entry else None


def update_queue(trace_id: str, result: Any) -> dict:
    """由 callback 端点调用，标记某 trace 已完成"""
    with _lock:
        if trace_id not in _queue:
            logger.warning({"msg": "callback 收到未知 trace_id", "trace_id": trace_id})
            return {
                "code": 400,
                "msg": f"{trace_id} 不存在于等待队列中",
                "data": None,
            }
        _queue[trace_id]["result"] = result
        _queue[trace_id]["completed"] = True
        _queue[trace_id]["updated_at"] = time.time()
    # 不打印 result 内容（可能含敏感数据）
    logger.info({"msg": "等待队列回调成功", "trace_id": trace_id})
    return {
        "code": 200,
        "msg": f"{trace_id} 回调成功",
        "data": None,
    }


def remove_queue(trace_id: str) -> bool:
    """从队列中移除条目；不存在返回 False，不抛异常"""
    with _lock:
        if trace_id not in _queue:
            return False
        del _queue[trace_id]
        return True
