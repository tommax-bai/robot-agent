"""Agent 框架基础类型：Task / SubTask / Plan / TaskResult + 错误体系 + Agent 协议。

Vision-Action 循环的共享数据类型（Observation / Action / Decision / ActionResult /
ActionOutcome）已下沉到 `services.contracts`，本模块以 re-export 方式保留历史
路径，调用方无需感知。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from services.contracts import Action, ActionOutcome, ActionResult, Decision, Observation

if TYPE_CHECKING:
    from runtime.ctx import RunContext


# ═══════════════════════════════════════════════════════
# 任务输入/输出
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class Task:
    """顶层任务输入。kind 自由字符串，语义由各 Supervisor job / API 约定。"""

    kind: str
    goal: str


@dataclass(frozen=True)
class SubTask:
    """Planner 拆解后的单个子任务"""

    id: str
    goal: str
    required_skill: str | None = None
    intent: str | None = None


@dataclass(frozen=True)
class Plan:
    """Planner 输出的执行计划。subtasks 不可变。"""

    subtasks: tuple[SubTask, ...]


@dataclass(frozen=True)
class TaskResult:
    """统一的任务结果，支持嵌套聚合。聚合策略见 `agents.result_aggregation`。"""

    ok: bool
    summary: str = ""
    sub_results: list[TaskResult] = field(default_factory=list)
    error: AgentError | None = None

    @classmethod
    def success(cls, summary: str = "") -> TaskResult:
        return cls(ok=True, summary=summary)

    @classmethod
    def failure(cls, error: AgentError) -> TaskResult:
        return cls(ok=False, error=error)


# ═══════════════════════════════════════════════════════
# 错误体系（层级化）
#
# AgentError
#  ├── ControlSignal      — 控制流信号：取消 / 步数耗尽，非真正的"出错"
#  │    ├── CancelledError
#  │    └── StepBudgetExceededError
#  ├── CapabilityError    — 本地能力故障，调用方通常无法自愈
#  │    ├── PlannerError
#  │    └── StrategistError
#  ├── UpstreamError      — 上游（LLM / 解析）故障，通常可重试
#  │    ├── LlmError
#  │    └── DecisionParseError
#  └── InputError         — 输入不合法 / 资源不存在
#       └── ToolNotFoundError
# ═══════════════════════════════════════════════════════


class AgentError(Exception):
    """所有 agent 框架异常的基类"""


class ControlSignal(AgentError):
    """控制流信号（取消、预算耗尽等），非真正的失败。"""


class CapabilityError(AgentError):
    """本地能力故障（Planner / Strategist 等），调用方通常无法自愈。"""


class UpstreamError(AgentError):
    """上游（LLM、解析器）故障，通常可重试。"""


class InputError(AgentError):
    """输入不合法 / 资源不存在。"""


class CancelledError(ControlSignal):
    pass


class StepBudgetExceededError(ControlSignal):
    """子任务步数预算耗尽，调用方可决定是否续航。"""


class PlannerError(CapabilityError):
    pass


class StrategistError(CapabilityError):
    pass


class LlmError(UpstreamError):
    pass


class DecisionParseError(UpstreamError):
    """无法从 LLM 响应解析出可执行的 Decision。"""


class ToolNotFoundError(InputError):
    pass


# ═══════════════════════════════════════════════════════
# Agent 协议
# ═══════════════════════════════════════════════════════


class Agent(Protocol):
    """所有 agent 实现的统一契约"""

    name: str

    async def run(self, task: Task, ctx: RunContext) -> TaskResult: ...


__all__ = [
    "Action",
    "ActionOutcome",
    "ActionResult",
    "Agent",
    "AgentError",
    "CancelledError",
    "CapabilityError",
    "ControlSignal",
    "Decision",
    "DecisionParseError",
    "InputError",
    "LlmError",
    "Observation",
    "Plan",
    "PlannerError",
    "StepBudgetExceededError",
    "StrategistError",
    "SubTask",
    "Task",
    "TaskResult",
    "ToolNotFoundError",
    "UpstreamError",
]
