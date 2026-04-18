"""RetryPolicy：把 StrategyResolver 的续航重试抽成可换策略。

默认 ResumeOnBudgetPolicy：策略抛 StepBudgetExceededError 时续航最多 N 次。
其他 AgentError / Exception 不在此处理（那些是真正的失败，应交给上层决定）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable, Protocol, TypeVar

import utils.logger as logger
from agents.base import StepBudgetExceededError

if TYPE_CHECKING:
    pass

T = TypeVar("T")


class RetryPolicy(Protocol):
    async def run(
        self,
        body: Callable[[], Awaitable[T]],
        *,
        trace_id: str,
        label: str = "",
    ) -> T: ...


class NoRetryPolicy:
    """什么都不重试，直接透传。用于 debug 或确认 flake 时的快速切换点。"""

    async def run(self, body, *, trace_id: str, label: str = ""):
        return await body()


class ResumeOnBudgetPolicy:
    """步数耗尽（StepBudgetExceededError）→ 续航最多 max_resumes 次。"""

    def __init__(self, max_resumes: int = 2):
        self._max_resumes = max_resumes

    async def run(self, body, *, trace_id: str, label: str = ""):
        last_error: StepBudgetExceededError | None = None
        for attempt in range(self._max_resumes + 1):
            try:
                return await body()
            except StepBudgetExceededError as e:
                last_error = e
                if attempt == self._max_resumes:
                    break
                logger.warning(
                    {
                        "msg": "子任务步数耗尽，发起续航",
                        "label": label,
                        "attempt": attempt + 1,
                        "max_resumes": self._max_resumes,
                    },
                    trace_id,
                )
        assert last_error is not None
        raise last_error
