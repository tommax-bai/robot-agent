"""
ScheduledJob Protocol + JobContext dataclass。

JobContext 是 Supervisor 注入给 Job 的"可见依赖"。Job 只能通过它调用
Supervisor.run_scheduled、生成 trace_id、读写 state、调用 strategist。
Supervisor 本身不再暴露 state/strategist/account_id 这些 public getter。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    from agents.base import Task, TaskResult
    from agents.strategist import Strategist
    from agents.supervisor.run_context_factory import RunContextFactory
    from services.state import AgentStateRepo


@dataclass
class JobContext:
    account_id: str
    state: AgentStateRepo
    strategist: Strategist
    run_scheduled: Callable[["Task", str], Awaitable["TaskResult"]]
    ctx_factory: RunContextFactory


class ScheduledJob(Protocol):
    name: str

    async def run(self, jctx: JobContext) -> None: ...
