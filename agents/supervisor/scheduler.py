"""
Scheduler: 自动调度循环与时段解析。

职责：
- 根据 daily_schedule 判断当前应运行的 scheduled job
- 在不同 AgentMode 下控制调度等待节奏
- 分派到 scheduled_jobs 中的具体作业
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.supervisor.scheduled_jobs import SCHEDULED_JOB_HANDLERS

if TYPE_CHECKING:
    from agents.supervisor.supervisor import Supervisor


def resolve_scheduled_task(now: datetime) -> str | None:
    """根据 daily_schedule 判断当前应执行的任务类型。"""
    schedule = config.agent["schedule"]["daily_schedule"]
    current_time = now.strftime("%H:%M")
    for slot in schedule:
        if slot["start"] <= current_time < slot["end"]:
            return slot["task"]
    return None


async def run_scheduler_loop(sv: Supervisor) -> None:
    """统一调度引擎：根据 daily_schedule 配置按时段分派任务。"""
    from agents.supervisor.supervisor import AgentMode

    logger.info({"msg": "统一调度器开始运行"})
    while True:
        try:
            if sv.mode == AgentMode.EXECUTING:
                await asyncio.sleep(10)
                continue
            if sv.mode in (AgentMode.DEBUG, AgentMode.WAITING):
                await asyncio.sleep(60)
                continue

            now = datetime.now()
            task_key = resolve_scheduled_task(now)
            if task_key is None:
                logger.info({"msg": f"当前是休息时间 ({now.strftime('%H:%M')})，待机中..."})
                await asyncio.sleep(300)
                continue

            handler = SCHEDULED_JOB_HANDLERS.get(task_key)
            if handler is None:
                logger.warning({"msg": "未知任务类型", "task_key": task_key})
                await asyncio.sleep(60)
                continue

            await handler(sv)

        except Exception as e:
            logger.error({"msg": "调度循环异常", "error": str(e)})
            await asyncio.sleep(60)
