"""
PostJob：发帖任务。带两道闸门：每日上限 + 冷启动知识量阈值。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import StrategistError, Task
from agents.strategist import GoalContext, Persona, StateSnapshot
from services.knowledge import get_evolution_context, harvest_knowledge

if TYPE_CHECKING:
    from agents.supervisor.jobs.base import JobContext


class PostJob:
    name = "post"

    def __init__(
        self,
        cold_start_threshold: int = 50,
        cold_start_backoff: int = 300,
        after_backoff: int = 120,
        strategist_failure_backoff: int = 60,
    ):
        self._cold_start_threshold = cold_start_threshold
        self._cold_start_backoff = cold_start_backoff
        self._after_backoff = after_backoff
        self._strategist_failure_backoff = strategist_failure_backoff

    async def run(self, jctx: JobContext) -> None:
        snapshot = jctx.state.get()

        today_posts = snapshot["daily_stats"]["posts_count"]
        max_posts = config.settings.agent.schedule.max_posts_per_day
        if today_posts >= max_posts:
            logger.info(
                {"msg": f"今日已发布 {today_posts}/{max_posts} 篇，本时段空闲", "account": jctx.account_id}
            )
            await asyncio.sleep(self._cold_start_backoff)
            return

        evo = get_evolution_context(jctx.state, snapshot["title_few_shots"])
        if evo.total_knowledge < self._cold_start_threshold:
            logger.info(
                {
                    "msg": "冷启动期，跳过发帖，等待知识积累",
                    "account": jctx.account_id,
                    "knowledge": evo.total_knowledge,
                    "threshold": self._cold_start_threshold,
                }
            )
            await asyncio.sleep(self._cold_start_backoff)
            return

        trace_id = f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        goal_ctx = GoalContext(
            persona=Persona.from_config(),
            state=StateSnapshot.from_state_dict(snapshot),
            inspiration=tuple(snapshot["inspiration_pool"]),
        )
        try:
            result = await jctx.strategist.generate("post", goal_ctx, trace_id=trace_id)
        except StrategistError as e:
            logger.warning(
                {
                    "msg": "发帖目标生成失败，跳过本轮",
                    "account": jctx.account_id,
                    "error": str(e),
                    "backoff_seconds": self._strategist_failure_backoff,
                },
                trace_id,
            )
            await asyncio.sleep(self._strategist_failure_backoff)
            return

        # post 路径当前不写 recent_searches（与旧行为一致）。若将来要写，此处加一行。
        logger.info(
            {"msg": "启动发帖任务", "account": jctx.account_id, "goal": result.goal}, trace_id
        )
        task = Task(kind="post", goal=result.goal)
        task_result = await jctx.run_scheduled(task, trace_id)

        if task_result.ok:
            jctx.state.update(is_posted=True, trace_id=trace_id)
        if task_result.summary:
            harvest_knowledge(task_result.summary, trace_id, jctx.state)

        logger.info(
            {"msg": "发帖任务完成", "account": jctx.account_id, "ok": task_result.ok}, trace_id
        )
        await asyncio.sleep(self._after_backoff)
