"""
Supervisor: 薄编排层。

职责（仅限于下列六项）：
- 组装 ModeState / RunSlot / RunContextFactory / Scheduler / JobRegistry
- 对外暴露 submit_task / run_scheduled / trigger_patrol 三个任务入口
- 对外暴露 switch_mode / start / stop / request_stop / status 四个生命周期入口
- 把模式切换引发的 cancel_current 显式写出来（不再藏在 set_mode 里）
- 为 Scheduler 派发 JobContext

不再做的事：
- 承担 dm/cr/post/patrol 的具体业务逻辑（搬到 agents/supervisor/jobs/）
- 对外暴露 state/strategist/current_run/account_id 等 private-ish 字段
- 在 mode 变更里偷偷 cancel 当前任务
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import AgentError, Task, TaskResult
from agents.supervisor.jobs import JobContext, JobRegistry, PatrolJob, PostJob, SimpleGoalJob
from agents.supervisor.mode_state import AgentMode, ModeState
from agents.supervisor.run_context_factory import RunContextFactory
from agents.supervisor.run_slot import RunHandle, RunSlot
from agents.supervisor.scheduler import Scheduler
from agents.supervisor.status import StatusSnapshot
from tools.chrome_local import cleanup_chrome_environment

if TYPE_CHECKING:
    from agents.operator.operator import Operator
    from agents.strategist import Strategist
    from runtime.events import EventBus
    from services.state import AgentStateRepo
    from tools.llm import LlmTool


class Supervisor:
    name = "supervisor"

    def __init__(
        self,
        operator: Operator,
        strategist: Strategist,
        state: AgentStateRepo,
        llm: LlmTool,
        event_bus: EventBus | None = None,
        account_id: str = "default",
    ):
        self._operator = operator
        self._strategist = strategist
        self._state = state
        self._account_id = account_id

        initial_mode = AgentMode.PATROLLING if config.settings.agent.default_mode == "patrolling" else AgentMode.WAITING
        self._mode = ModeState(initial=initial_mode, event_bus=event_bus, account_id=account_id)
        self._slot = RunSlot(event_bus=event_bus, account_id=account_id)
        self._ctx_factory = RunContextFactory(state=state, llm=llm)

        self._patrol_job = PatrolJob()
        registry = JobRegistry()
        registry.register(self._patrol_job)
        registry.register(SimpleGoalJob("dm", "私信"))
        registry.register(SimpleGoalJob("cr", "评论"))
        registry.register(PostJob())

        self._scheduler = Scheduler(
            mode_state=self._mode,
            registry=registry,
            job_context_factory=self._make_job_ctx,
            account_id=account_id,
        )

        logger.info(
            {"msg": "Supervisor 初始化完成", "account": account_id, "mode": self._mode.mode.value}
        )

    # ── 对外只读属性（仅限向外暴露模式 + 是否忙）───────────────

    @property
    def mode(self) -> AgentMode:
        return self._mode.mode

    def is_busy(self) -> bool:
        return self._slot.is_busy()

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        """启动调度器（幂等；同步入口，内部 create_task）"""
        self._scheduler.start()

    def request_stop(self, reason: str = "external") -> None:
        """同步路径触发停止：取消 slot + 取消 scheduler task。供信号处理器等不能 await 的调用方使用。"""
        self._slot.cancel_current(reason)
        self._scheduler.request_stop_nonblocking()

    async def stop(self) -> None:
        """优雅关闭：先停调度循环，再 drain 当前任务。"""
        logger.info({"msg": "Supervisor 正在关闭", "account": self._account_id})
        await self._scheduler.stop()
        await self._slot.drain()
        logger.info({"msg": "Supervisor 已关闭", "account": self._account_id})

    # ── 模式切换 ──────────────────────────────────────────────

    def switch_mode(self, new: AgentMode) -> None:
        """切模式；进入 DEBUG/WAITING 时显式取消当前任务。"""
        self._mode.transition_to(new)
        if new in (AgentMode.DEBUG, AgentMode.WAITING):
            self._slot.cancel_current(f"mode→{new.value}")

    # ── 任务入口 ──────────────────────────────────────────────

    async def submit_task(self, task: Task, trace_id: str | None = None) -> TaskResult:
        """外部入口：执行一次性任务。抢占当前任务；期间把模式切成 EXECUTING。"""
        if self._mode.mode == AgentMode.DEBUG:
            return TaskResult.failure(AgentError("当前处于调试模式，拒绝执行任务", code="debug_mode"))

        await self._slot.preempt(cleanup=cleanup_chrome_environment)

        prev_mode = self._mode.mode
        self._mode.transition_to(AgentMode.EXECUTING)
        try:
            return await self._run_with_slot(task, trace_id)
        finally:
            self._mode.transition_to(prev_mode)

    async def run_scheduled(self, task: Task, trace_id: str) -> TaskResult:
        """调度器内部入口：不抢占、不切模式（调度循环自身保证串行）。"""
        return await self._run_with_slot(task, trace_id)

    async def trigger_patrol(self, trace_id: str) -> TaskResult:
        """API 一次性触发人设巡逻。"""
        if self._mode.is_patrolling():
            raise RuntimeError("patrol 自动调度正在运行，请先关闭 patrol 再手动触发")
        from agents.base import StrategistError

        try:
            result = await self._patrol_job.build_goal(self._make_job_ctx(), trace_id)
        except StrategistError as e:
            return TaskResult.failure(AgentError(f"巡逻目标生成失败: {e}", code="strategist_error"))

        # 手动触发也遵循 "副作用由 job 所在层级决定" —— 与 PatrolJob.run 保持一致
        if result.used_topic:
            self._state.update(recent_searches=[result.used_topic], trace_id=trace_id)

        logger.info(
            {"msg": "按人设手动触发 patrol", "account": self._account_id, "goal": result.goal}, trace_id
        )
        return await self.submit_task(Task(kind="patrol", goal=result.goal), trace_id=trace_id)

    # ── 状态查询 ──────────────────────────────────────────────

    def status(self) -> StatusSnapshot:
        run = self._slot.current
        now = datetime.now()
        return StatusSnapshot(
            mode=self._mode.mode.value,
            is_active=run is not None,
            task_kind=run.task.kind if run else "idle",
            current_slot=self._scheduler.current_slot_kind(now) or "rest",
            current_trace_id=run.trace_id if run else None,
            current_goal=run.task.goal if run else None,
            running_seconds=run.elapsed_seconds if run else 0,
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    # ── 内部辅助 ──────────────────────────────────────────────

    async def _run_with_slot(self, task: Task, trace_id: str | None) -> TaskResult:
        actual_trace = trace_id or self._ctx_factory.new_trace_id()
        ctx = self._ctx_factory.create(actual_trace)
        handle = RunHandle(task=task, ctx=ctx)
        async with self._slot.claim(handle) as run:
            try:
                run.result = await self._operator.run(task, run.ctx)
                return run.result
            except BaseException as e:
                run.error = e
                raise

    def _make_job_ctx(self) -> JobContext:
        return JobContext(
            account_id=self._account_id,
            state=self._state,
            strategist=self._strategist,
            run_scheduled=self.run_scheduled,
            ctx_factory=self._ctx_factory,
        )
