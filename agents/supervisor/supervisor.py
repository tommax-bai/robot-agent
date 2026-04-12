"""
Supervisor: 调度与编排核心。

职责：
- 维护当前模式（PATROLLING / WAITING / EXECUTING / DEBUG）
- 接收外部任务请求，抢占当前任务
- 启动和停止自动调度循环
- 提供状态查询给 API 层
- 为子组件提供 RunContext 工厂
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import AgentError, Task, TaskResult
from runtime.context import CancelToken, RunContext
from tools.cleanup import cleanup_chrome_environment

if TYPE_CHECKING:
    from agents.operator.operator import Operator
    from agents.strategist import Strategist
    from services.agent_state import AgentStateRepo
    from tools.llm_tool import LlmTool


class AgentMode(Enum):
    WAITING = "waiting"
    PATROLLING = "patrolling"
    EXECUTING = "executing"
    DEBUG = "debug"


class ActiveRun:
    """
    一次任务执行的句柄：包含 trace_id、任务、cancel token、开始时间、完成事件。
    Supervisor 同一时刻只持有一个 ActiveRun。
    """

    def __init__(self, task: Task, ctx: RunContext):
        self.task = task
        self.ctx = ctx
        self.start_time = time.time()
        self.done = asyncio.Event()

    @property
    def trace_id(self) -> str:
        return self.ctx.trace_id


class Supervisor:
    name = "supervisor"

    def __init__(
        self,
        operator: Operator,
        strategist: Strategist,
        state: AgentStateRepo,
        llm: LlmTool,
    ):
        self._operator = operator
        self._strategist = strategist
        self._state = state
        self._llm = llm

        self._mode = (
            AgentMode.PATROLLING
            if config.agent["default_mode"] == "patrolling"
            else AgentMode.WAITING
        )

        self._current_run: ActiveRun | None = None
        self._scheduler_task: asyncio.Task | None = None

        logger.info({"msg": "Supervisor 初始化完成", "mode": self._mode.value})

    # ── 公开依赖访问 ──────────────────────────────────────────
    # scheduler/scheduled_jobs 等子组件通过这些属性获取依赖，避免触碰私有字段

    @property
    def state(self) -> AgentStateRepo:
        return self._state

    @property
    def strategist(self) -> Strategist:
        return self._strategist

    def make_ctx(self, trace_id: str) -> RunContext:
        """为子组件构造一个干净的 RunContext（独立的 cancel token）"""
        return RunContext(
            trace_id=trace_id,
            cancel=CancelToken(),
            state=self._state,
            llm=self._llm,
        )

    # ── 模式管理 ──────────────────────────────────────────────

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def set_mode(self, mode: AgentMode) -> None:
        old = self._mode
        self._mode = mode
        logger.info({"msg": "Agent 模式切换", "old": old.value, "new": mode.value})
        if mode in (AgentMode.DEBUG, AgentMode.WAITING) and self._current_run:
            self._current_run.ctx.cancel.cancel(f"mode→{mode.value}")

    # ── 调度循环 ──────────────────────────────────────────────

    def start_scheduler(self) -> None:
        if self._scheduler_task is None or self._scheduler_task.done():
            from agents.supervisor.scheduler import run_scheduler_loop
            self._scheduler_task = asyncio.create_task(run_scheduler_loop(self))
            logger.info({"msg": "统一调度器已启动"})

    def request_cancel(self, reason: str = "external") -> None:
        """非阻塞地请求停止当前一切活动：调度循环 + 当前任务的 cancel token。

        给信号处理器（SIGINT/SIGTERM）等"不能 await"的调用方使用。
        实际等待退出由调用方在 await 上下文里另行处理（例如 shutdown）。
        """
        if self._current_run is not None:
            self._current_run.ctx.cancel.cancel(reason)
        if self._scheduler_task is not None and not self._scheduler_task.done():
            self._scheduler_task.cancel()

    async def shutdown(self) -> None:
        """优雅关闭：取消调度循环、终止当前任务、等待退出"""
        logger.info({"msg": "Supervisor 正在关闭"})
        # 1. 取消调度循环
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except (asyncio.CancelledError, Exception):
                pass

        # 2. 取消正在执行的任务并等待退出
        if self._current_run:
            self._current_run.ctx.cancel.cancel("supervisor_shutdown")
            try:
                await asyncio.wait_for(self._current_run.done.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning({"msg": "等待任务退出超时", "trace_id": self._current_run.trace_id})

        logger.info({"msg": "Supervisor 已关闭"})

    # ── 任务执行 ──────────────────────────────────────────────

    async def execute_task(
        self,
        task: Task,
        trace_id: str | None = None,
    ) -> TaskResult:
        """外部入口：执行一个临时任务。会抢占当前在跑的自动任务。"""
        if self._mode == AgentMode.DEBUG:
            return TaskResult.failure(AgentError("当前处于调试模式，拒绝执行任务", code="debug_mode"))

        await self._preempt_current()

        prev_mode = self._mode
        self.set_mode(AgentMode.EXECUTING)
        try:
            with self._claim_run(task, trace_id) as run:
                return await self._operator.run(task, run.ctx)
        finally:
            self.set_mode(prev_mode)

    async def execute_scheduled_task(self, task: Task, trace_id: str) -> TaskResult:
        """
        调度器内部入口：用于跑 patrol/dm/cr/post 等定时任务。
        不切 mode，不抢占（调度器自己保证不并发）。
        """
        with self._claim_run(task, trace_id) as run:
            return await self._operator.run(task, run.ctx)

    async def _preempt_current(self) -> None:
        """通知旧任务取消并等待其真正退出"""
        if self._current_run is None:
            return
        old_run = self._current_run
        logger.info({"msg": "抢占当前任务", "trace_id": old_run.trace_id})
        old_run.ctx.cancel.cancel("preempted")
        cleanup_chrome_environment(old_run.trace_id)
        try:
            await asyncio.wait_for(old_run.done.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning({
                "msg": "等待旧任务退出超时",
                "trace_id": old_run.trace_id,
            })

    @contextmanager
    def _claim_run(self, task: Task, trace_id: str | None = None):
        """占用 run 槽位，退出时自动释放并 set done event"""
        actual_trace = trace_id or str(uuid.uuid4())
        ctx = self.make_ctx(actual_trace)
        run = ActiveRun(task=task, ctx=ctx)
        self._current_run = run
        try:
            yield run
        finally:
            run.done.set()
            if self._current_run is run:
                self._current_run = None

    # ── 状态查询 ──────────────────────────────────────────────

    def get_status(self) -> dict:
        run = self._current_run
        now = datetime.now()
        return {
            "mode": self._mode.value,
            "is_active": run is not None,
            "task_kind": run.task.kind if run else "idle",
            "current_slot": self.current_scheduled_task(now) or "rest",
            "current_trace_id": run.trace_id if run else None,
            "current_goal": run.task.goal if run else None,
            "running_seconds": int(time.time() - run.start_time) if run else 0,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def current_scheduled_task(self, now: datetime) -> str | None:
        """根据 daily_schedule 判断当前应执行的任务类型"""
        from agents.supervisor.scheduler import resolve_scheduled_task
        return resolve_scheduled_task(now)
