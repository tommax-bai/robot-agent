"""
SimpleGoalJob：dm / cr 这类目标来自 config.schedule.task_goals 的简单任务。
不需要 strategist，只是 "读配置 → 直接跑"。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import Task

if TYPE_CHECKING:
    from agents.supervisor.jobs.base import JobContext


class SimpleGoalJob:
    def __init__(self, name: str, label: str, post_backoff: int = 60):
        self.name = name
        self._label = label
        self._post_backoff = post_backoff

    async def run(self, jctx: JobContext) -> None:
        trace_id = jctx.ctx_factory.new_trace_id(self.name)
        goal = config.settings.agent.schedule.task_goals[self.name]
        logger.info({"msg": f"启动{self._label}任务", "account": jctx.account_id, "goal": goal}, trace_id)
        task = Task(kind=self.name, goal=goal)
        result = await jctx.run_scheduled(task, trace_id)
        logger.info({"msg": f"{self._label}处理完成", "account": jctx.account_id, "ok": result.ok}, trace_id)
        await asyncio.sleep(self._post_backoff)
