"""PostGoalGenerator：发帖目标生成器。

合成公式：brainstorm → 选 topic → 取最近 10 条标题样本 → 渲染 posting_goal.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.strategist.context import GoalResult

if TYPE_CHECKING:
    from agents.strategist.brainstormer import Brainstormer
    from agents.strategist.context import GoalContext
    from agents.strategist.renderer import PromptRenderer
    from agents.strategist.selector import Selector


class PostGoalGenerator:
    kind = "post"

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
        topics = await self._brainstormer.brainstorm(goal_ctx, trace_id=trace_id)
        topic = self._selector.choose(topics)

        title_few_shots = goal_ctx.state.title_few_shots[-10:]
        title_few_shots_str = (
            "\n".join(f"- {t}" for t in title_few_shots) if title_few_shots else "（暂无样本，请自由发挥）"
        )

        goal = await self._renderer.render_and_call(
            "posting_goal.md",
            trace_id=trace_id,
            default_temperature=0.8,
            persona_name=goal_ctx.persona.name,
            persona_character=goal_ctx.persona.character,
            persona_style=goal_ctx.persona.style,
            core_strategy=goal_ctx.persona.strategy,
            topic=topic,
            recruitment_info=goal_ctx.persona.recruitment_info,
            title_few_shots=title_few_shots_str,
            last_discovery=goal_ctx.state.last_discovery or "（暂无近期收割内容）",
        )

        return GoalResult(
            goal=goal,
            kind=self.kind,
            template="posting_goal.md",
            used_topic=topic,
        )
