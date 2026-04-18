"""
运行时上下文与取消作用域。

- RunContext：显式传递的执行上下文（trace_id / cancel scope / state / llm）。
- CancelScope：协作式取消令牌，树形传播。退出 with 块时自动从父作用域解链，
  避免长寿命根作用域的 children 列表无限增长。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.base import CancelledError

if TYPE_CHECKING:
    from services.state import AgentStateRepo
    from tools.llm import LlmTool


class CancelScope:
    """协作式取消令牌。父作用域取消会传播到所有子作用域。

    推荐作为上下文管理器使用：`with parent.child() as scope: ...`，
    退出时自动从父的 children 中移除，长寿命根作用域不会泄露子引用。
    """

    __slots__ = ("_cancelled", "_reason", "_children", "_parent")

    def __init__(self, parent: "CancelScope | None" = None):
        self._cancelled = False
        self._reason = ""
        self._children: list[CancelScope] = []
        self._parent = parent
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

    def child(self) -> "CancelScope":
        return CancelScope(parent=self)

    def unlink(self) -> None:
        """从父作用域解链。空操作时忽略。"""
        if self._parent is None:
            return
        try:
            self._parent._children.remove(self)
        except ValueError:
            pass
        self._parent = None

    def __enter__(self) -> "CancelScope":
        return self

    def __exit__(self, *_exc) -> None:
        self.unlink()


@dataclass
class RunContext:
    """显式执行上下文。派生子上下文时 cancel scope 树形传播。"""

    trace_id: str
    cancel: CancelScope
    state: "AgentStateRepo"
    llm: "LlmTool"

    def child(self, *, trace_id: str | None = None) -> "RunContext":
        return RunContext(
            trace_id=trace_id or self.trace_id,
            cancel=self.cancel.child(),
            state=self.state,
            llm=self.llm,
        )


__all__ = ["CancelScope", "CancelledError", "RunContext"]
