"""
SubtaskRunner: 单个子任务的执行 + 续航重试。

执行链路：
1. 优先走 RecipeOperator 快路径（命中即返回，跳过 LLM）
2. 未命中则委托给注入的 SubtaskStrategy（VisionActionStep / AgentBayDelegateStrategy / ...）
3. Strategy 抛 StepBudgetExceededError 时按 max_resumes 续航重试

Strategy 的具体实现由 mode 决定，Runner 自身不感知是哪种 mode。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import StepBudgetExceededError, SubTask, TaskResult

if TYPE_CHECKING:
    from agents.operator.recipe_operator import RecipeOperator
    from agents.operator.strategy import SubtaskStrategy
    from runtime.context import RunContext
    from services.action_memory import BehaviorSummarizer


class SubtaskRunner:
    name = "subtask_runner"

    def __init__(
        self,
        strategy: SubtaskStrategy,
        recipe_operator: RecipeOperator | None = None,
        behavior_summarizer: BehaviorSummarizer | None = None,
        max_resumes: int = 2,
    ):
        self._strategy = strategy
        self._recipe_operator = recipe_operator
        self._behavior_summarizer = behavior_summarizer
        self._max_resumes = max_resumes

    def set_strategy(self, strategy: SubtaskStrategy) -> None:
        """替换子任务执行策略（用于 runtime mode 热切换）。"""
        self._strategy = strategy

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        if self._recipe_operator is not None and self._recipe_operator.has_recipes:
            attempt = await self._recipe_operator.try_run(subtask, ctx)
            if attempt.ok and attempt.recipe is not None:
                return TaskResult.success(attempt.recipe.summary or f"recipe {attempt.recipe.id} executed")
            logger.info(
                {
                    "msg": f"动作 recipe 未完成，回退 strategy={self._strategy.name}",
                    "status": attempt.status,
                    "reason": attempt.reason,
                    "recipe_id": attempt.recipe.id if attempt.recipe else None,
                },
                ctx.trace_id,
            )

        last_error: Exception | None = None
        for attempt in range(self._max_resumes + 1):
            try:
                result = await self._strategy.run(subtask, ctx)
                if result.ok and self._behavior_summarizer is not None:
                    self._behavior_summarizer.submit_trace(ctx.trace_id, subtask.id, subtask.intent)
                return result
            except StepBudgetExceededError as e:
                last_error = e
                if attempt == self._max_resumes:
                    break
                logger.warning(
                    {
                        "msg": "子任务步数耗尽，发起续航",
                        "subtask": subtask.id,
                        "attempt": attempt + 1,
                        "max_resumes": self._max_resumes,
                    },
                    ctx.trace_id,
                )
        # 所有重试都失败
        from agents.base import AgentError

        return TaskResult.failure(
            error=last_error if isinstance(last_error, AgentError) else StepBudgetExceededError("max resumes exhausted")
        )
