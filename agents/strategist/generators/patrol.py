"""PatrolGoalGenerator：巡逻目标生成器。

合成公式：brainstorm → 选 topic → 用 evolution 权重决定首页/搜索倾向 → 渲染 patrol_goal.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import StrategistError
from agents.strategist.context import GoalResult

if TYPE_CHECKING:
    from agents.strategist.brainstormer import Brainstormer
    from agents.strategist.context import GoalContext
    from agents.strategist.renderer import PromptRenderer
    from agents.strategist.selector import Selector


class PatrolGoalGenerator:
    kind = "patrol"

    def __init__(
        self,
        brainstormer: Brainstormer,
        renderer: PromptRenderer,
        selector: Selector,
    ):
        self._brainstormer = brainstormer
        self._renderer = renderer
        self._selector = selector

    async def generate(self, goal_ctx: GoalContext, *, trace_id: str) -> GoalResult:
        if goal_ctx.evolution is None:
            raise StrategistError("patrol goal 需要 evolution context")

        topics = await self._brainstormer.brainstorm(goal_ctx, trace_id=trace_id)
        search_keyword = self._selector.choose(topics)
        is_home_focus = self._selector.roll() < goal_ctx.evolution.home_weight

        goal = await self._renderer.render_and_call(
            "patrol_goal.md",
            trace_id=trace_id,
            default_temperature=0.3,
            persona_name=goal_ctx.persona.name,
            persona_character=goal_ctx.persona.character,
            current_time_str=goal_ctx.now.strftime("%Y-%m-%d %H:%M:%S"),
            total_knowledge=goal_ctx.evolution.total_knowledge,
            home_weight_pct=f"{goal_ctx.evolution.home_weight * 100:.0f}",
            search_weight_pct=f"{goal_ctx.evolution.search_weight * 100:.0f}",
            focus_desc="【首页沉浸】" if is_home_focus else "【主动探索】",
            search_keyword=search_keyword,
            mood=goal_ctx.state.mood,
        )

        # 原实现把 topics[0] 写入 recent_searches（与实际选中的 search_keyword 不一致）
        # 此处返回 used_topic=topics[0] 以保留旧行为；调用方决定是否写 state
        return GoalResult(
            goal=goal,
            kind=self.kind,
            template="patrol_goal.md",
            used_topic=topics[0],
        )
