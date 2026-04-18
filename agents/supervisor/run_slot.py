"""
RunSlot + RunHandle：单槽位的任务运行管理。

Supervisor 同一时刻只允许一个任务在跑。RunSlot 负责：
- claim() / release() 占用与释放槽位（事件总线发 task_started / task_completed）
- preempt() 通知老任务 cancel 并等它退出（超时视为硬错误，而非静默警告）
- cancel_current() 供外部同步路径（信号处理、模式切换）触发取消

RunHandle 在 release 时记录 result / error，让调用 preempt 的一方可以
观察到老任务退出的最终状态。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Callable

import utils.logger as logger
from utils.events import Ev, ev

if TYPE_CHECKING:
    from agents.base import Task, TaskResult
    from runtime.ctx import RunContext
    from runtime.events import EventBus


class RunHandle:
    """单次任务执行的句柄。Supervisor 同一时刻只持有一个。"""

    def __init__(self, task: Task, ctx: RunContext):
        self.task = task
        self.ctx = ctx
        self.start_time = time.time()
        self.done = asyncio.Event()
        self.result: TaskResult | None = None
        self.error: BaseException | None = None

    @property
    def trace_id(self) -> str:
        return self.ctx.trace_id

    @property
    def elapsed_seconds(self) -> int:
        return int(time.time() - self.start_time)


class RunSlot:
    def __init__(
        self,
        event_bus: EventBus | None,
        account_id: str = "default",
        preempt_timeout: float = 10.0,
        drain_timeout: float = 5.0,
    ):
        self._events = event_bus
        self._account_id = account_id
        self._preempt_timeout = preempt_timeout
        self._drain_timeout = drain_timeout
        self._current: RunHandle | None = None

    @property
    def current(self) -> RunHandle | None:
        return self._current

    def is_busy(self) -> bool:
        return self._current is not None

    def cancel_current(self, reason: str) -> None:
        """同步路径（信号处理 / set_mode）用：只发 cancel，不等退出。"""
        if self._current is not None:
            self._current.ctx.cancel.cancel(reason)

    async def preempt(self, cleanup: Callable[[str], None] | None = None) -> None:
        """通知当前任务取消并等它真正退出。超时抛 RuntimeError（硬错误）。"""
        if self._current is None:
            return
        old = self._current
        ev(Ev.TASK_PREEMPTED, trace_id=old.trace_id, account_id=self._account_id,
           kind=old.task.kind, elapsed_ms=int(old.elapsed_seconds * 1000))
        old.ctx.cancel.cancel("preempted")
        if cleanup is not None:
            try:
                cleanup(old.trace_id)
            except Exception as e:
                logger.warning(
                    {
                        "msg": "抢占前 cleanup 异常，继续等待旧任务退出",
                        "account": self._account_id,
                        "trace_id": old.trace_id,
                        "error": str(e),
                    }
                )
        try:
            await asyncio.wait_for(old.done.wait(), timeout=self._preempt_timeout)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"抢占超时（{self._preempt_timeout}s）：旧任务 trace={old.trace_id} 未退出，"
                "拒绝并发执行；请检查是否存在同步阻塞调用"
            ) from e

    async def drain(self) -> None:
        """shutdown 路径：通知取消并等当前任务退出；超时只 warning，不抛（已经要关闭了）。"""
        if self._current is None:
            return
        run = self._current
        run.ctx.cancel.cancel("supervisor_shutdown")
        try:
            await asyncio.wait_for(run.done.wait(), timeout=self._drain_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                {
                    "msg": "等待任务退出超时",
                    "account": self._account_id,
                    "trace_id": run.trace_id,
                }
            )

    @asynccontextmanager
    async def claim(self, handle: RunHandle) -> AsyncIterator[RunHandle]:
        """占用槽位，退出时自动释放并 set done event。"""
        self._current = handle
        # 把 trace_id 压入 ContextVar：整棵子任务树（包括 asyncio.create_task 派生）
        # 都能被日志自动带上，调用方不必再显式传 trace_id。
        trace_token = logger.set_trace_id(handle.trace_id)
        self._publish_task_started(handle)
        try:
            yield handle
        finally:
            handle.done.set()
            if self._current is handle:
                self._current = None
            self._publish_task_completed(handle)
            logger.finalize_trace(handle.trace_id)
            logger.reset_trace_id(trace_token)

    def _publish_task_started(self, handle: RunHandle) -> None:
        ev(Ev.TASK_STARTED, trace_id=handle.trace_id, account_id=self._account_id,
           kind=handle.task.kind, goal=handle.task.goal)
        if self._events is None:
            return
        from runtime.events import Event, payload

        self._events.publish(
            Event.TASK_STARTED,
            payload(trace_id=handle.trace_id, kind=handle.task.kind, goal=handle.task.goal),
        )

    def _publish_task_completed(self, handle: RunHandle) -> None:
        # ok/error 反映在 handle.result / handle.error 上：有 error 算 failed，
        # 没有 result 也算 failed（取消/超时路径）。
        ok = handle.result is not None and handle.result.ok and handle.error is None
        elapsed_ms = int(handle.elapsed_seconds * 1000)
        if ok:
            ev(Ev.TASK_COMPLETED, trace_id=handle.trace_id, account_id=self._account_id,
               kind=handle.task.kind, elapsed_ms=elapsed_ms)
        else:
            err_str = str(handle.error) if handle.error else (
                str(handle.result.error) if handle.result and handle.result.error else "unknown"
            )
            ev(Ev.TASK_FAILED, trace_id=handle.trace_id, account_id=self._account_id,
               kind=handle.task.kind, elapsed_ms=elapsed_ms, error=err_str)
        if self._events is None:
            return
        from runtime.events import Event, payload

        self._events.publish(
            Event.TASK_COMPLETED,
            payload(
                trace_id=handle.trace_id,
                kind=handle.task.kind,
                elapsed_seconds=handle.elapsed_seconds,
            ),
        )
