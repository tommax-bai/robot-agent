"""
PatrolJob：Persona 驱动的巡逻任务。

统一的 build_goal / run_once 让"调度循环自动跑"和"API 一次性触发"复用
同一份 strategist 调用代码，避免分叉。
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import StrategistError, Task
from agents.strategist import GoalContext, Persona, StateSnapshot
from services.knowledge import get_evolution_context, harvest_knowledge
from utils.events import Ev, ev

if TYPE_CHECKING:
    from agents.strategist import GoalResult
    from agents.supervisor.jobs.base import JobContext


def emit_persona(persona: Persona, *, kind: str, account_id: str, trace_id: str) -> None:
    """发一条 task.persona 事件，dashboard 在启动对应 kind 任务前展示人设卡片。"""
    ev(
        Ev.TASK_PERSONA,
        trace_id=trace_id,
        account_id=account_id,
        kind=kind,
        name=persona.name,
        character=persona.character,
        style=persona.style,
        strategy=persona.strategy,
    )


class PatrolJob:
    name = "patrol"

    def __init__(
        self,
        strategist_failure_backoff: int = 60,
        rest_range_cfg_key: str = "patrol_rest_between_rounds",
    ):
        self._strategist_failure_backoff = strategist_failure_backoff
        self._rest_range_cfg_key = rest_range_cfg_key

    async def build_goal(self, jctx: JobContext, trace_id: str) -> GoalResult:
        """从 state + strategist 合成本轮巡逻目标。失败抛 StrategistError。"""
        snapshot = jctx.state.get()
        persona = Persona.from_config()
        emit_persona(persona, kind="patrol", account_id=jctx.account_id, trace_id=trace_id)
        goal_ctx = GoalContext(
            persona=persona,
            state=StateSnapshot.from_state_dict(snapshot),
            inspiration=tuple(snapshot["inspiration_pool"]),
            evolution=get_evolution_context(jctx.state, snapshot["title_few_shots"]),
        )
        return await jctx.strategist.generate("patrol", goal_ctx, trace_id=trace_id)

    async def run(self, jctx: JobContext) -> None:
        """调度循环入口：生成目标 → run_scheduled → 收编知识 → 休息。"""
        trace_id = jctx.ctx_factory.new_trace_id("patrol")
        try:
            result = await self.build_goal(jctx, trace_id)
        except StrategistError as e:
            logger.warning(
                {
                    "msg": "巡逻目标生成失败，跳过本轮",
                    "account": jctx.account_id,
                    "error": str(e),
                    "backoff_seconds": self._strategist_failure_backoff,
                },
                trace_id,
            )
            await asyncio.sleep(self._strategist_failure_backoff)
            return

        # 副作用上移：strategist 不再偷写 state，由 job 根据 GoalResult 显式处理
        if result.used_topic:
            jctx.state.update(recent_searches=[result.used_topic], trace_id=trace_id)

        logger.info(
            {"msg": "启动巡逻任务", "account": jctx.account_id, "goal": result.goal}, trace_id
        )
        task = Task(kind="patrol", goal=result.goal)
        task_result = await jctx.run_scheduled(task, trace_id)

        if task_result.summary:
            harvest_knowledge(task_result.summary, trace_id, jctx.state)

        logger.info(
            {"msg": "一轮巡逻完成", "account": jctx.account_id, "ok": task_result.ok}, trace_id
        )

        rest_range = getattr(config.settings.agent.schedule, self._rest_range_cfg_key)
        wait = random.randint(rest_range[0], rest_range[1])
        logger.info({"msg": f"巡逻休息 {wait}秒 ({wait / 60:.1f}分钟)", "account": jctx.account_id})
        await asyncio.sleep(wait)
