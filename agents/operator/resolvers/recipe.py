"""RecipeResolver：把 RecipeOperator 包成标准 SubtaskResolver。

命中即 resolved；没 recipe / 没命中 → skipped（让下一层接管）；
recipe 步骤跑起来但失败 → failed（recipe 是"已验证"的路径，失败本身是信号，不应再回退到 LLM 视觉循环）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import AgentError, TaskResult
from agents.operator.resolver import ResolverOutcome

if TYPE_CHECKING:
    from agents.base import SubTask
    from agents.operator.recipe_operator import RecipeOperator
    from runtime.ctx import RunContext


class RecipeResolver:
    name = "recipe"

    def __init__(self, recipe_op: RecipeOperator):
        self._recipe_op = recipe_op

    async def resolve(self, subtask: SubTask, ctx: RunContext) -> ResolverOutcome:
        if not self._recipe_op.has_recipes:
            return ResolverOutcome.skipped("no recipes loaded")

        attempt = await self._recipe_op.try_run(subtask, ctx)

        if attempt.ok and attempt.recipe is not None:
            summary = attempt.recipe.summary or f"recipe {attempt.recipe.id} executed"
            return ResolverOutcome.resolved(TaskResult.success(summary))

        # 保持旧行为：recipe 未命中 / 中途失败 → 回退到下一层 Resolver（strategy）。
        # 旧 SubtaskRunner 也是任何失败都回退，这里保留而非改成 "recipe 失败即 failed"。
        logger.info(
            {
                "msg": "动作 recipe 未完成，交给下一个 Resolver",
                "status": attempt.status,
                "reason": attempt.reason,
                "recipe_id": attempt.recipe.id if attempt.recipe else None,
            },
            ctx.trace_id,
        )
        return ResolverOutcome.skipped(attempt.reason or "recipe not completed")


__all__ = ["RecipeResolver", "AgentError"]
