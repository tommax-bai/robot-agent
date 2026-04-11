"""
SubtaskRunner: 单个子任务的执行 + 续航重试。

把"步数耗尽时是否续航"的策略从 vision_action 循环里剥离出来。
重试条件用结构化异常 StepBudgetExceededError，不再字符串匹配。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import StepBudgetExceededError, SubTask, TaskResult

if TYPE_CHECKING:
    from agents.operator.vision_action import VisionActionStep
    from runtime.context import RunContext


class SubtaskRunner:
    name = "subtask_runner"

    def __init__(self, vision_step: VisionActionStep, max_resumes: int = 2):
        self._step = vision_step
        self._max_resumes = max_resumes

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        last_error: Exception | None = None
        for attempt in range(self._max_resumes + 1):
            try:
                return await self._step.run(subtask, ctx)
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
