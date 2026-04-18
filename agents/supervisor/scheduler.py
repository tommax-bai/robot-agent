"""
Scheduler: 统一调度循环 + 时段解析（类化版本）。

职责：
- 根据 config.agent.schedule.daily_schedule 判断当前应运行的 ScheduledJob
- 根据 ModeState 控制等待节奏（EXECUTING 暂停、DEBUG/WAITING 60s 空转、休息时段 5min）
- 分派到 JobRegistry 注册的具体 ScheduledJob 实例

调度循环在独立的 asyncio.Task 里跑。start() / stop() 控制生命周期。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.supervisor.mode_state import AgentMode

if TYPE_CHECKING:
    from agents.supervisor.jobs.base import JobContext
    from agents.supervisor.jobs.registry import JobRegistry
    from agents.supervisor.mode_state import ModeState


class Scheduler:
    def __init__(
        self,
        mode_state: ModeState,
        registry: JobRegistry,
        job_context_factory,  # Callable[[], JobContext] —— 每轮新建 JobContext
        account_id: str = "default",
    ):
        self._mode = mode_state
        self._registry = registry
        self._make_job_ctx = job_context_factory
        self._account_id = account_id
        self._task: asyncio.Task | None = None

    # ── 时段解析 ──────────────────────────────────────────────

    def current_slot_kind(self, now: datetime | None = None) -> str | None:
        return self._resolve_slot(now or datetime.now())

    @staticmethod
    def _resolve_slot(now: datetime) -> str | None:
        schedule = config.settings.agent.schedule.daily_schedule
        current_time = now.strftime("%H:%M")
        for slot in schedule:
            if slot["start"] <= current_time < slot["end"]:
                return slot["task"]
        return None

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动调度循环（幂等）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())
            logger.info({"msg": "统一调度器已启动", "account": self._account_id})

    def request_stop_nonblocking(self) -> None:
        """同步路径触发：只 cancel task，不等。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def stop(self) -> None:
        """取消调度循环并等待退出。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── 主循环 ────────────────────────────────────────────────

    async def run(self) -> None:
        logger.info({"msg": "统一调度器开始运行", "account": self._account_id})
        while True:
            try:
                if self._mode.is_executing():
                    await asyncio.sleep(10)
                    continue
                if self._mode.is_idle():
                    await asyncio.sleep(60)
                    continue

                now = datetime.now()
                task_key = self._resolve_slot(now)
                if task_key is None:
                    logger.info(
                        {
                            "msg": f"当前是休息时间 ({now.strftime('%H:%M')})，待机中...",
                            "account": self._account_id,
                        }
                    )
                    await asyncio.sleep(300)
                    continue

                job = self._registry.get(task_key)
                if job is None:
                    logger.warning(
                        {"msg": "未知任务类型", "account": self._account_id, "task_key": task_key}
                    )
                    await asyncio.sleep(60)
                    continue

                await job.run(self._make_job_ctx())

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error({"msg": "调度循环异常", "account": self._account_id, "error": str(e)})
                await asyncio.sleep(60)
