"""事件目录 + 结构化事件日志辅助函数。

这是全系统日志内容的单一真相源：所有可聚合的状态变迁都从 `Ev` 里挑一个名字，
避免自由文案带来的漂移（同一事件两处写法不一致时无法聚合）。

约定：
- 事件名（`Ev.*`）一律 `lower_case.dot.separated`，动作在最后：`task.started`、`recipe.hit`。
- 每个事件的默认等级写在 `_LEVEL` 里；要临时调，传 `level=logging.WARNING`。
- 字段命名唯一真相：`trace_id` / `account_id` / `goal` / `sub_goal` / `intent` /
  `elapsed_ms` / `ok` / `error` / `model`。不要再用 `account` / `user_goal` / `elapsed_seconds` / `success`。
- 错误事件（`*.failed`）：`error=str(exc)`，配合 `exc_info=True` 附堆栈。
- `msg=` 可选，人类可读短句；**关键信息放字段**，msg 不是聚合入口。
- 单一 owner：同一事件只由一个地方发布，避免重复日志。例如 `task.started` 只由
  `agents/supervisor/run_slot.py` 发，API 层发 `api.task.received`。

用法：
    from utils.events import ev, Ev
    ev(Ev.TASK_STARTED, account_id=acc, goal=task.goal, kind=task.kind)
    ev(Ev.LLM_FAILED, model="gemini-2.5-pro", error=str(e), exc_info=True)
"""
from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

_BRIDGE = logging.getLogger("my_logger")


class Ev(StrEnum):
    # System
    SYS_STARTUP = "system.startup"
    SYS_SHUTDOWN = "system.shutdown"

    # API
    API_TASK_RECEIVED = "api.task.received"

    # Task lifecycle（Supervisor 独家 owner）
    TASK_STARTED = "task.started"
    TASK_PREEMPTED = "task.preempted"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_PERSONA = "task.persona"

    # Plan
    PLAN_STARTED = "plan.started"
    PLAN_GENERATED = "plan.generated"
    PLAN_FAILED = "plan.failed"
    PLAN_INTENT_RESOLVED = "plan.intent_resolved"
    PLAN_SKILL_INVALID = "plan.skill_invalid"

    # Subtask
    SUBTASK_STARTED = "subtask.started"
    SUBTASK_COMPLETED = "subtask.completed"
    SUBTASK_FAILED = "subtask.failed"
    SUBTASK_RETRY = "subtask.retry"

    # Recipe
    RECIPE_HIT = "recipe.hit"
    RECIPE_MISS = "recipe.miss"
    RECIPE_PROMOTED = "recipe.promoted"
    RECIPE_MINED = "recipe.mined"

    # Vision loop（默认 DEBUG；生产环境关）
    VISION_OBSERVED = "vision.observed"
    VISION_DECIDED = "vision.decided"
    VISION_FINISHED = "vision.finished"

    # Actions
    ACTION_TOOL_INVOKED = "action.tool.invoked"
    ACTION_TOOL_FAILED = "action.tool.failed"
    ACTION_ATOMIC_OK = "action.atomic.ok"
    ACTION_ATOMIC_FAILED = "action.atomic.failed"

    # LLM
    LLM_CALLED = "llm.called"
    LLM_RETRY = "llm.retry"
    LLM_FAILED = "llm.failed"

    # Strategy (agentbay 委托)
    STRATEGY_DELEGATED = "strategy.delegated"
    STRATEGY_RETURNED = "strategy.returned"
    STRATEGY_CANCELLED = "strategy.cancelled"
    STRATEGY_FAILED = "strategy.failed"

    # Backend
    BACKEND_SCREENSHOT = "backend.screenshot"
    BACKEND_SESSION_ACQUIRED = "backend.session.acquired"
    BACKEND_SESSION_RELEASED = "backend.session.released"

    # Scheduler
    SCHED_TICK_FAILED = "scheduler.tick_failed"

    # Chrome
    CHROME_LAUNCHED = "chrome.launched"
    CHROME_RECONNECTED = "chrome.reconnected"
    CHROME_LOST = "chrome.lost"


_LEVEL: dict[str, int] = {
    # DEBUG：高频、可压缩
    Ev.VISION_OBSERVED: logging.DEBUG,
    Ev.VISION_DECIDED: logging.DEBUG,
    Ev.ACTION_ATOMIC_OK: logging.DEBUG,
    Ev.LLM_CALLED: logging.DEBUG,
    Ev.BACKEND_SCREENSHOT: logging.DEBUG,
    # WARNING：降级但仍可服务
    Ev.SUBTASK_RETRY: logging.WARNING,
    Ev.LLM_RETRY: logging.WARNING,
    Ev.PLAN_SKILL_INVALID: logging.WARNING,
    Ev.RECIPE_MISS: logging.WARNING,
    Ev.STRATEGY_CANCELLED: logging.WARNING,
    Ev.CHROME_LOST: logging.WARNING,
    # ERROR：请求级失败
    Ev.TASK_FAILED: logging.ERROR,
    Ev.PLAN_FAILED: logging.ERROR,
    Ev.SUBTASK_FAILED: logging.ERROR,
    Ev.LLM_FAILED: logging.ERROR,
    Ev.ACTION_TOOL_FAILED: logging.ERROR,
    Ev.ACTION_ATOMIC_FAILED: logging.ERROR,
    Ev.STRATEGY_FAILED: logging.ERROR,
    Ev.SCHED_TICK_FAILED: logging.ERROR,
    # 其它默认 INFO
}


def ev(event: Ev | str, *, level: int | None = None, msg: str = "",
       exc_info: bool = False, **fields: Any) -> None:
    """发一条结构化事件。

    - `event`：Ev 枚举或字符串，写入 `structured["event"]`。
    - `level`：省略则查 `_LEVEL`，缺省 INFO。
    - `msg`：可选人类可读短句；主要信息放字段。
    - `exc_info=True`：附堆栈（配合 `*.failed`）。
    - 其它 kwargs 作为结构化字段；`trace_id` 不必传，ContextVar 自动带。
    """
    # 延迟 import 避免循环
    from utils.logger import _trace_var

    name = event.value if isinstance(event, Ev) else str(event)
    lvl = level if level is not None else _LEVEL.get(name, logging.INFO)
    tid = str(fields.pop("trace_id", None) or _trace_var.get() or "")
    structured = {"event": name, **fields}
    extra = {"trace_id": tid, "structured": structured}
    _BRIDGE.log(lvl, msg, extra=extra, exc_info=exc_info, stacklevel=2)


__all__ = ["Ev", "ev"]
