"""StopPolicy：Operator 子任务 for 循环何时提前终止。

把原 Operator.run 里 "if not result.ok: break" 的隐式策略显式化。
未来要支持 ContinueOnFailure / StopAfterN 等模式只需再加一个实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agents.base import TaskResult


class StopPolicy(Protocol):
    def should_stop(self, results: list[TaskResult]) -> bool: ...


class StopOnFirstFailure:
    """首个失败的子任务就终止。Operator 的默认且历史行为。"""

    def should_stop(self, results: list[TaskResult]) -> bool:
        return bool(results) and not results[-1].ok


class ContinueAlways:
    """所有子任务都执行，不管单步是否失败。"""

    def should_stop(self, results: list[TaskResult]) -> bool:
        return False
