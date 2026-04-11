"""
任务轮次：调度循环 + 各类定时任务的执行流程。

每个 round 函数声明式注册到 _ROUND_HANDLERS。
通过 supervisor 的公开接口（state, strategist）获取依赖，不再触碰私有字段。
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


# ═══════════════════════════════════════════════════════
# 调度循环
# ═══════════════════════════════════════════════════════

async def run_schedule_loop(sv: Supervisor) -> None:
    """统一调度引擎：根据 daily_schedule 配置按时段分派任务"""
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
            slot = sv.current_task_slot(now)
            if slot is None:
                logger.info({"msg": f"当前是休息时间 ({now.strftime('%H:%M')})，待机中..."})
                await asyncio.sleep(300)
                continue

            handler = _ROUND_HANDLERS.get(slot)
            if handler is None:
                logger.warning({"msg": "未知任务类型", "slot": slot})
                await asyncio.sleep(60)
                continue

            await handler(sv)

        except Exception as e:
            logger.error({"msg": "调度循环异常", "error": str(e)})
            await asyncio.sleep(60)


# ═══════════════════════════════════════════════════════
# Round handlers
# ═══════════════════════════════════════════════════════

async def patrol_round(sv: "Supervisor") -> None:
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
        logger.warning({
            "msg": "巡逻目标生成失败，跳过本轮",
            "error": str(e),
            "backoff_seconds": _STRATEGIST_FAILURE_BACKOFF,
        }, trace_id)
        await asyncio.sleep(_STRATEGIST_FAILURE_BACKOFF)
        return

    logger.info({"msg": "启动巡逻任务", "goal": goal}, trace_id)
    task = Task(kind="patrol", goal=goal)
    result = await sv.execute_round(task, trace_id=trace_id)

    if result.summary:
        harvest_knowledge(result.summary, trace_id, sv.state)

    logger.info({"msg": "一轮巡逻完成", "ok": result.ok}, trace_id)

    rest_range = config.agent["maintenance"]["patrol_rest_between_rounds"]
    wait = random.randint(rest_range[0], rest_range[1])
    logger.info({"msg": f"巡逻休息 {wait}秒 ({wait/60:.1f}分钟)"})
    await asyncio.sleep(wait)


async def _simple_round(sv: "Supervisor", task_key: str, label: str) -> None:
    """通用简单任务（dm/cr）：直接读 config.task_goals，无需 strategist"""
    trace_id = f"{task_key}-{uuid.uuid4().hex[:8]}"
    goal = config.agent["maintenance"]["task_goals"][task_key]
    logger.info({"msg": f"启动{label}任务", "goal": goal}, trace_id)
    task = Task(kind=task_key, goal=goal)  # type: ignore[arg-type]
    result = await sv.execute_round(task, trace_id=trace_id)
    logger.info({"msg": f"{label}处理完成", "ok": result.ok}, trace_id)
    await asyncio.sleep(60)


async def dm_round(sv: "Supervisor") -> None:
    await _simple_round(sv, task_key="dm", label="私信")


async def cr_round(sv: "Supervisor") -> None:
    await _simple_round(sv, task_key="cr", label="评论")


# 发帖冷启动门槛：当 total_knowledge 低于此值时跳过发帖，等待积累
_POST_COLD_START_THRESHOLD = 50
_POST_COLD_START_BACKOFF = 300
_POST_AFTER_BACKOFF = 120


async def post_round(sv: "Supervisor") -> None:
    snapshot = sv.state.get()

    # 1. 每日上限检查（直接复用 state.daily_stats，无需 supervisor 维护副本）
    today_posts = snapshot["daily_stats"]["posts_count"]
    max_posts = config.agent["maintenance"]["max_posts_per_day"]
    if today_posts >= max_posts:
        logger.info({"msg": f"今日已发布 {today_posts}/{max_posts} 篇，本时段空闲"})
        await asyncio.sleep(_POST_COLD_START_BACKOFF)
        return

    # 2. 冷启动门槛
    evo = get_evolution_context(sv.state, snapshot["title_few_shots"])
    if evo.total_knowledge < _POST_COLD_START_THRESHOLD:
        logger.info({
            "msg": "冷启动期，跳过发帖，等待知识积累",
            "knowledge": evo.total_knowledge,
            "threshold": _POST_COLD_START_THRESHOLD,
        })
        await asyncio.sleep(_POST_COLD_START_BACKOFF)
        return

    trace_id = f"post-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # 3. 生成目标
    try:
        goal = await sv.strategist.generate_posting_goal(
            inspiration_pool=snapshot["inspiration_pool"],
            trace_id=trace_id,
        )
    except StrategistError as e:
        logger.warning({
            "msg": "发帖目标生成失败，跳过本轮",
            "error": str(e),
            "backoff_seconds": _STRATEGIST_FAILURE_BACKOFF,
        }, trace_id)
        await asyncio.sleep(_STRATEGIST_FAILURE_BACKOFF)
        return

    # 4. 执行
    logger.info({"msg": "启动发帖任务", "goal": goal}, trace_id)
    task = Task(kind="post", goal=goal)
    result = await sv.execute_round(task, trace_id=trace_id)

    # 5. 标记已发帖（state 内部维护 daily_stats.posts_count++）+ 收割知识
    if result.ok:
        sv.state.update(is_posted=True, trace_id=trace_id)
    if result.summary:
        harvest_knowledge(result.summary, trace_id, sv.state)

    logger.info({"msg": "发帖任务完成", "ok": result.ok}, trace_id)
    await asyncio.sleep(_POST_AFTER_BACKOFF)


# ═══════════════════════════════════════════════════════
# 任务类型 → handler 注册表
# ═══════════════════════════════════════════════════════

_ROUND_HANDLERS: dict[str, Callable[["Supervisor"], Awaitable[None]]] = {
    "patrol": patrol_round,
    "dm": dm_round,
    "cr": cr_round,
    "post": post_round,
}
