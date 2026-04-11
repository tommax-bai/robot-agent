"""
Agent 框架基础类型：Task, TaskResult, SubTask, Plan, AgentError 体系。
所有 agent 共享同一套数据形态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


# ═══════════════════════════════════════════════════════
# 任务输入/输出
# ═══════════════════════════════════════════════════════

TaskKind = Literal["patrol", "post", "dm", "cr", "user_goal"]


@dataclass(frozen=True)
class Task:
    """顶层任务输入"""
    kind: TaskKind
    goal: str


@dataclass(frozen=True)
class SubTask:
    """Planner 拆解后的单个子任务"""
    id: str
    goal: str
    required_skill: str | None = None


@dataclass(frozen=True)
class Plan:
    """Planner 输出的执行计划"""
    subtasks: list[SubTask]


@dataclass(frozen=True)
class TaskResult:
    """统一的任务结果，支持嵌套聚合"""
    ok: bool
    summary: str = ""
    sub_results: list["TaskResult"] = field(default_factory=list)
    error: "AgentError | None" = None

    @classmethod
    def success(cls, summary: str = "") -> "TaskResult":
        return cls(ok=True, summary=summary)

    @classmethod
    def failure(cls, error: "AgentError") -> "TaskResult":
        return cls(ok=False, error=error)

    @classmethod
    def aggregate(cls, results: list["TaskResult"]) -> "TaskResult":
        all_ok = all(r.ok for r in results)
        last_summary = next((r.summary for r in reversed(results) if r.summary), "")
        last_error = next((r.error for r in reversed(results) if r.error), None)
        return cls(ok=all_ok, summary=last_summary, sub_results=results, error=last_error)


# ═══════════════════════════════════════════════════════
# 错误体系
# ═══════════════════════════════════════════════════════

class AgentError(Exception):
    """所有 agent 框架异常的基类"""
    code: str = "agent_error"
    recoverable: bool = False

    def __init__(self, message: str = "", *, code: str | None = None, recoverable: bool | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if recoverable is not None:
            self.recoverable = recoverable


class CancelledError(AgentError):
    code = "cancelled"
    recoverable = False


class LlmError(AgentError):
    code = "llm_error"
    recoverable = True


class PlannerError(AgentError):
    code = "planner_error"
    recoverable = False


class StrategistError(AgentError):
    code = "strategist_error"
    recoverable = True


class StepBudgetExceededError(AgentError):
    """子任务步数预算耗尽，调用方可决定是否续航"""
    code = "step_budget_exceeded"
    recoverable = True


class DecisionParseError(AgentError):
    """无法从 LLM 响应解析出可执行的 Decision"""
    code = "decision_parse_error"
    recoverable = True


class ToolNotFoundError(AgentError):
    code = "tool_not_found"
    recoverable = False


# ═══════════════════════════════════════════════════════
# Agent 协议
# ═══════════════════════════════════════════════════════

class Agent(Protocol):
    """所有 agent 实现的统一契约"""
    name: str

    async def run(self, task: Task, ctx: "RunContext") -> TaskResult: ...


# ═══════════════════════════════════════════════════════
# Vision-Action 循环用的数据结构
# ═══════════════════════════════════════════════════════

@dataclass(frozen=True)
class Observation:
    """单次观察的快照"""
    image_base64: str
    captured_at: str  # ISO 时间字符串


@dataclass(frozen=True)
class Action:
    """单个原子动作"""
    method: str
    params: dict[str, Any]
    delay: float | None = None


@dataclass(frozen=True)
class Decision:
    """LLM 一次决策的解析结果"""
    thought: str
    actions: list[Action]
    raw: str  # 保留原始文本用于日志

    @classmethod
    def parse(cls, llm_output: Any, raw_text: str) -> "Decision":
        """
        从 LLM 解析后的对象（dict 或 list）构造 Decision。
        在这一层做所有的脏数据清洗：键名归一化、字段名变体、坐标键名兜底等。
        失败抛 DecisionParseError。
        """
        actions_raw, thought = cls._extract_actions_and_thought(llm_output)
        if not actions_raw:
            raise DecisionParseError(f"未从 LLM 输出中识别到任何 action: {raw_text[:200]}")

        actions: list[Action] = []
        for a in actions_raw:
            if not isinstance(a, dict) or "method" not in a:
                continue
            params = cls._normalize_params(a.get("params") or {})
            method = a["method"]
            if method in ("click", "dblclick", "move") and "x" not in params:
                # LLM 偶尔输出带空格或引号的键名
                for k, v in list(params.items()):
                    lk = k.lower()
                    if "x" in lk:
                        params["x"] = v
                    if "y" in lk:
                        params["y"] = v
            actions.append(Action(method=method, params=params, delay=a.get("delay")))

        if not actions:
            raise DecisionParseError(f"所有 action 都缺少 method: {raw_text[:200]}")

        return cls(thought=thought, actions=actions, raw=raw_text)

    @staticmethod
    def _extract_actions_and_thought(obj: Any) -> tuple[list[dict], str]:
        """支持四种 LLM 输出形态"""
        if isinstance(obj, list):
            thought = obj[0].get("thought", "") if obj and isinstance(obj[0], dict) else ""
            if obj and isinstance(obj[0], dict) and isinstance(obj[0].get("actions"), list):
                return [a for a in obj[0]["actions"] if isinstance(a, dict)], thought
            return [a for a in obj if isinstance(a, dict)], thought
        if not isinstance(obj, dict):
            return [], ""
        thought = obj.get("thought", "")
        if obj.get("method") == "execute_sequence":
            inner = obj.get("params", {}).get("actions", [])
            return [a for a in inner if isinstance(a, dict)], thought
        if "actions" in obj and isinstance(obj["actions"], list):
            return [a for a in obj["actions"] if isinstance(a, dict)], thought
        if "method" in obj:
            return [obj], thought
        return [], thought

    @staticmethod
    def _normalize_params(params: dict) -> dict:
        if not isinstance(params, dict):
            return {}
        return {k.strip().strip('"').strip("'").strip(): v for k, v in params.items()}


@dataclass(frozen=True)
class ActionResult:
    """单个动作执行后的结果"""
    method: str
    ok: bool
    message: str = ""
    payload: Any = None


@dataclass(frozen=True)
class ActionOutcome:
    """一拍动作执行的最终结果"""
    results: list[ActionResult]
    is_finish: bool = False
    summary: str = ""

    @classmethod
    def finished(cls, summary: str, results: list[ActionResult]) -> "ActionOutcome":
        return cls(results=results, is_finish=True, summary=summary)

    @classmethod
    def continuing(cls, results: list[ActionResult]) -> "ActionOutcome":
        return cls(results=results, is_finish=False)


# 避免循环 import：RunContext 在 runtime/context.py 中定义
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from runtime.context import RunContext
