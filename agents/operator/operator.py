"""
Operator Agent：任务编排入口。

职责：plan + 依次执行子任务。执行细节（recipe vs strategy、重试、观察副作用）
由 SubtaskRunner 内部的 Resolver 链完成；本类只关心"拿到 Plan → 循环跑 → 聚合"。

设计原则：
- 依赖（Planner, SubtaskRunner, StopPolicy）通过构造注入
- StopPolicy 决定子任务 for 循环何时提前终止，默认 StopOnFirstFailure
- 不再 import supervisor 检查 abort —— 使用 ctx.cancel
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import AgentError, Task, TaskResult
from agents.operator.stop_policy import StopOnFirstFailure
from agents.result_aggregation import aggregate_results
from services.history import init_history
from utils.events import Ev, ev

if TYPE_CHECKING:
    from agents.operator.stop_policy import StopPolicy
    from agents.operator.subtask_runner import SubtaskRunner
    from agents.planner.planner import Planner
    from runtime.ctx import RunContext


class Operator:
    name = "operator"

    def __init__(
        self,
        planner: Planner,
        runner: SubtaskRunner,
        stop_policy: StopPolicy | None = None,
    ):
        self._planner = planner
        self._runner = runner
        self._stop_policy = stop_policy or StopOnFirstFailure()

    async def run(self, task: Task, ctx: RunContext) -> TaskResult:
        init_history(ctx.trace_id, task.goal)

        ev(Ev.PLAN_STARTED, goal=task.goal)
        try:
            plan = await self._planner.generate_plan(ctx, task.goal)
        except AgentError as e:
            ev(Ev.PLAN_FAILED, error=str(e), exc_info=True)
            return TaskResult.failure(e)

        ev(Ev.PLAN_GENERATED,
           subtask_count=len(plan.subtasks),
           subtasks=[
               {"id": s.id, "goal": s.goal, "skill": s.required_skill, "intent": s.intent}
               for s in plan.subtasks
           ])

        results: list[TaskResult] = []
        for i, subtask in enumerate(plan.subtasks):
            ctx.cancel.raise_if_cancelled()

            ev(Ev.SUBTASK_STARTED, index=i + 1, total=len(plan.subtasks),
               sub_goal=subtask.goal, skill=subtask.required_skill, intent=subtask.intent)

            sub_ctx = ctx.child()
            result = await self._runner.run(subtask, sub_ctx)
            results.append(result)

            if result.ok:
                ev(Ev.SUBTASK_COMPLETED, index=i + 1, sub_goal=subtask.goal, intent=subtask.intent)
            else:
                ev(Ev.SUBTASK_FAILED, index=i + 1, sub_goal=subtask.goal, intent=subtask.intent,
                   error=str(result.error))
            if self._stop_policy.should_stop(results):
                break

        return aggregate_results(results)
