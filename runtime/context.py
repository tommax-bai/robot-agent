"""
运行时上下文：CancelToken, ConversationHistory, RunContext。

设计原则：
- RunContext 是显式传递的，没有任何全局可变状态
- 取消用 token 协作式传播（树形）
- 对话历史是注入的对象，每个 task 一份
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents.base import CancelledError

if TYPE_CHECKING:
    from services.agent_state import AgentStateRepo
    from tools.llm_tool import LlmTool


# ═══════════════════════════════════════════════════════
# 取消令牌
# ═══════════════════════════════════════════════════════

class CancelToken:
    """
    协作式取消：被调用方在合适的检查点调 raise_if_cancelled()。
    支持树形传播：父 token 取消会同步取消所有 child。
    """
    def __init__(self, parent: "CancelToken | None" = None):
        self._cancelled = False
        self._reason: str = ""
        self._children: list[CancelToken] = []
        if parent is not None:
            parent._children.append(self)
            if parent._cancelled:
                self.cancel(parent._reason)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str:
        return self._reason

    def cancel(self, reason: str = "") -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._reason = reason
        for child in self._children:
            child.cancel(reason)

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError(self._reason or "task cancelled")

    def child(self) -> "CancelToken":
        """派生子 token，父取消会传播到子"""
        return CancelToken(parent=self)


# ═══════════════════════════════════════════════════════
# 对话历史
# ═══════════════════════════════════════════════════════

@dataclass
class ConversationHistory:
    """
    Vision-action 循环的对话历史。
    每个子任务一份独立实例，不再依赖全局可变 dict。
    """
    max_rounds: int = 6
    system_message: dict[str, Any] = field(default_factory=dict)
    user_messages: list[dict[str, Any]] = field(default_factory=list)
    assistant_messages: list[dict[str, Any]] = field(default_factory=list)

    def set_system(self, content: str) -> None:
        self.system_message = {"role": "system", "content": content}

    def add_user(self, content: list[dict[str, Any]]) -> None:
        self.user_messages.append({"role": "user", "content": content})
        self.user_messages = self.user_messages[-self.max_rounds:]

    def add_assistant(self, raw: str) -> None:
        self.assistant_messages.append({"role": "assistant", "content": raw})
        self.assistant_messages = self.assistant_messages[-self.max_rounds:]

    def to_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.system_message:
            msgs.append(self.system_message)
        msgs.extend(self.user_messages)
        msgs.extend(self.assistant_messages)
        return msgs


# ═══════════════════════════════════════════════════════
# 运行时上下文
# ═══════════════════════════════════════════════════════

@dataclass
class RunContext:
    """
    显式传递的运行时上下文。包含 trace_id、取消令牌、状态仓储、LLM 工具。
    子 agent 调用时使用 ctx.child() 派生子上下文。
    """
    trace_id: str
    cancel: CancelToken
    state: "AgentStateRepo"
    llm: "LlmTool"

    def child(self, *, trace_id: str | None = None) -> "RunContext":
        """
        派生子上下文：复用同一个 state/llm，cancel token 树形传播。
        """
        return RunContext(
            trace_id=trace_id or self.trace_id,
            cancel=self.cancel.child(),
            state=self.state,
            llm=self.llm,
        )


