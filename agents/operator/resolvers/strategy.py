"""StrategyResolver：把 SubtaskStrategy + RetryPolicy 包成 SubtaskResolver。

这是 Resolver 链里的"兜底层"：永远不 skip，总是尝试解决（成功 → resolved，
失败 → failed）。运行时 mode 热切换只需要 swap 里面的 strategy 引用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import AgentError, StepBudgetExceededError, TaskResult
from agents.operator.resolver import ResolverOutcome

if TYPE_CHECKING:
    from agents.base import SubTask
    from agents.operator.retry_policy import RetryPolicy
    from agents.operator.strategy import SubtaskStrategy
    from runtime.ctx import RunContext


class StrategyResolver:
    name = "strategy"

    def __init__(self, strategy: SubtaskStrategy, retry: RetryPolicy):
        self._strategy = strategy
        self._retry = retry

    def set_strategy(self, strategy: SubtaskStrategy) -> None:
        """替换子任务执行策略（runtime mode 热切换）。"""
        self._strategy = strategy

    @property
    def strategy(self) -> SubtaskStrategy:
        return self._strategy

    async def resolve(self, subtask: SubTask, ctx: RunContext) -> ResolverOutcome:
        try:
            result = await self._retry.run(
                lambda: self._strategy.run(subtask, ctx),
                trace_id=ctx.trace_id,
                label=subtask.id,
            )
            return ResolverOutcome.resolved(result)
        except StepBudgetExceededError as e:
            return ResolverOutcome.failed(TaskResult.failure(e), reason=str(e))
        except AgentError as e:
            return ResolverOutcome.failed(TaskResult.failure(e), reason=str(e))
