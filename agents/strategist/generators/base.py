"""GoalGenerator Protocol：目标生成器的统一契约。

新增 goal 类型 = 加一个 Generator 类 + 注册，旧代码零改动（参考 Supervisor 的 JobRegistry）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agents.strategist.context import GoalContext, GoalResult


class GoalGenerator(Protocol):
    kind: str

    async def generate(self, goal_ctx: GoalContext, *, trace_id: str) -> GoalResult: ...
