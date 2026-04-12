"""
Scheduled jobs: 自动调度器触发的具体作业。

每个 job 负责准备业务目标、调用 Supervisor 执行 Task，并处理作业级收尾逻辑。
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable

import config
import utils.logger as logger
from agents.base import StrategistError, Task
from services.knowledge import get_evolution_context, harvest_knowledge

if TYPE_CHECKING:
    from agents.supervisor.supervisor import Supervisor


_STRATEGIST_FAILURE_BACKOFF = 60
_POST_COLD_START_THRESHOLD = 50
_POST_COLD_START_BACKOFF = 300
_POST_AFTER_BACKOFF = 120


async def run_patrol_job(sv: Supervisor) -> None:
    trace_id = f"patrol-{uuid.uuid4().hex[:8]}"
    snapshot = sv.state.get()
    evo = get_evolution_context(sv.state, snapshot["title_few_shots"])

    try:
        goal = await sv.strategist.generate_patrol_goal(
            inspiration_pool=snapshot["inspiration_pool"],
            evolution=evo,
            trace_id=trace_id,
        )
    except StrategistError as e:
        logger.warning(
            {
                "msg": "巡逻目标生成失败，跳过本轮",
                "error": str(e),
                "backoff_seconds": _STRATEGIST_FAILURE_BACKOFF,
            },
            trace_id,
        )
        await asyncio.sleep(_STRATEGIST_FAILURE_BACKOFF)
        return

    logger.info({"msg": "启动巡逻任务", "goal": goal}, trace_id)
    task = Task(kind="patrol", goal=goal)
    result = await sv.execute_scheduled_task(task, trace_id=trace_id)

    if result.summary:
        harvest_knowledge(result.summary, trace_id, sv.state)

    logger.info({"msg": "一轮巡逻完成", "ok": result.ok}, trace_id)

    rest_range = config.agent["schedule"]["patrol_rest_between_rounds"]
    wait = random.randint(rest_range[0], rest_range[1])
    logger.info({"msg": f"巡逻休息 {wait}秒 ({wait / 60:.1f}分钟)"})
    await asyncio.sleep(wait)


async def _run_simple_job(sv: Supervisor, task_key: str, label: str) -> None:
    """通用简单任务（dm/cr）：直接读 config.task_goals，无需 strategist。"""
    trace_id = f"{task_key}-{uuid.uuid4().hex[:8]}"
    goal = config.agent["schedule"]["task_goals"][task_key]
    logger.info({"msg": f"启动{label}任务", "goal": goal}, trace_id)
    task = Task(kind=task_key, goal=goal)  # type: ignore[arg-type]
    result = await sv.execute_scheduled_task(task, trace_id=trace_id)
    logger.info({"msg": f"{label}处理完成", "ok": result.ok}, trace_id)
    await asyncio.sleep(60)


async def run_dm_job(sv: Supervisor) -> None:
    await _run_simple_job(sv, task_key="dm", label="私信")


async def run_comment_reply_job(sv: Supervisor) -> None:
    await _run_simple_job(sv, task_key="cr", label="评论")


async def run_post_job(sv: Supervisor) -> None:
    snapshot = sv.state.get()

    today_posts = snapshot["daily_stats"]["posts_count"]
    max_posts = config.agent["schedule"]["max_posts_per_day"]
    if today_posts >= max_posts:
        logger.info({"msg": f"今日已发布 {today_posts}/{max_posts} 篇，本时段空闲"})
        await asyncio.sleep(_POST_COLD_START_BACKOFF)
        return

    evo = get_evolution_context(sv.state, snapshot["title_few_shots"])
    if evo.total_knowledge < _POST_COLD_START_THRESHOLD:
        logger.info(
            {
                "msg": "冷启动期，跳过发帖，等待知识积累",
                "knowledge": evo.total_knowledge,
                "threshold": _POST_COLD_START_THRESHOLD,
            }
        )
        await asyncio.sleep(_POST_COLD_START_BACKOFF)
        return

    trace_id = f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    try:
        goal = await sv.strategist.generate_posting_goal(
            inspiration_pool=snapshot["inspiration_pool"],
            trace_id=trace_id,
        )
    except StrategistError as e:
        logger.warning(
            {
                "msg": "发帖目标生成失败，跳过本轮",
                "error": str(e),
                "backoff_seconds": _STRATEGIST_FAILURE_BACKOFF,
            },
            trace_id,
        )
        await asyncio.sleep(_STRATEGIST_FAILURE_BACKOFF)
        return

    logger.info({"msg": "启动发帖任务", "goal": goal}, trace_id)
    task = Task(kind="post", goal=goal)
    result = await sv.execute_scheduled_task(task, trace_id=trace_id)

    if result.ok:
        sv.state.update(is_posted=True, trace_id=trace_id)
    if result.summary:
        harvest_knowledge(result.summary, trace_id, sv.state)

    logger.info({"msg": "发帖任务完成", "ok": result.ok}, trace_id)
    await asyncio.sleep(_POST_AFTER_BACKOFF)


SCHEDULED_JOB_HANDLERS: dict[str, Callable[[Supervisor], Awaitable[None]]] = {
    "patrol": run_patrol_job,
    "dm": run_dm_job,
    "cr": run_comment_reply_job,
    "post": run_post_job,
}
