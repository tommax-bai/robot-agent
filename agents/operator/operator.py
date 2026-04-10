"""
Operator Agent：任务编排入口。

职责：plan + 依次执行子任务。具体的视觉-动作循环和重试逻辑被委托给
SubtaskRunner / VisionActionStep。

设计原则：
- 依赖（Planner, SubtaskRunner）通过构造注入
- 不再 import supervisor 检查 abort —— 使用 ctx.cancel
- 不再创建 SkillRegistry / Planner —— 由 container 在启动时构造
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import Task, TaskResult, AgentError
from services.history import init_history

if TYPE_CHECKING:
    from agents.operator.subtask_runner import SubtaskRunner
    from agents.planner.planner import Planner
    from runtime.context import RunContext


class Operator:
    name = "operator"

    def __init__(self, planner: "Planner", runner: "SubtaskRunner"):
        self._planner = planner
        self._runner = runner

    async def run(self, task: Task, ctx: "RunContext") -> TaskResult:
        init_history(ctx.trace_id, task.goal)

        logger.info({"msg": "开始任务规划", "goal": task.goal}, ctx.trace_id)
        try:
            plan = self._planner.generate_plan(ctx, task.goal)
        except AgentError as e:
            logger.error({"msg": "任务规划失败", "error": str(e)}, ctx.trace_id)
            return TaskResult.failure(e)

        logger.info(
            {"msg": "规划完成", "subtask_count": len(plan.subtasks)},
            ctx.trace_id,
        )

        results: list[TaskResult] = []
        for i, subtask in enumerate(plan.subtasks):
            ctx.cancel.raise_if_cancelled()

            logger.info(
                {
                    "msg": f"执行子任务 {i+1}/{len(plan.subtasks)}",
                    "sub_goal": subtask.goal,
                    "skill": subtask.required_skill,
                },
                ctx.trace_id,
            )

            sub_ctx = ctx.child()
            result = await self._runner.run(subtask, sub_ctx)
            results.append(result)

            if not result.ok:
                logger.error(
                    {"msg": "子任务执行失败", "error": str(result.error)},
                    ctx.trace_id,
                )
                break

        return TaskResult.aggregate(results)
