"""Brainstormer：单一职责 —— 从 GoalContext 产出话题候选列表。

从 public 方法降级为 internal primitive：不再对外暴露，由 GoalGenerator 按需调用。
原 Strategist 的 brainstorm 会被 patrol/post 各自再调一次；新设计让生成器
决定是否要 brainstorm，避免 LLM 调用放大。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import StrategistError

if TYPE_CHECKING:
    from agents.strategist.context import GoalContext
    from agents.strategist.renderer import PromptRenderer


class Brainstormer:
    def __init__(self, renderer: PromptRenderer):
        self._renderer = renderer

    async def brainstorm(self, goal_ctx: GoalContext, *, trace_id: str) -> list[str]:
        """返回话题字符串列表。空结果抛 StrategistError。"""
        time_context = f"当前时间: {goal_ctx.now.strftime('%Y-%m-%d %H:%M')}, 星期{goal_ctx.now.isoweekday()}"
        text = await self._renderer.render_and_call(
            "brainstorm.md",
            trace_id=trace_id,
            default_temperature=0.9,
            persona_name=goal_ctx.persona.name,
            core_strategy=goal_ctx.persona.strategy,
            time_context=time_context,
            inspiration_keywords=", ".join(goal_ctx.inspiration[:10]) or "（暂无，请自由发散）",
            anxiety_keywords=", ".join(goal_ctx.state.anxiety_keywords[:8]) or "（暂无）",
            knowledge_keywords=", ".join(goal_ctx.state.knowledge_topics[:8]) or "（暂无）",
            forbidden_words=", ".join(goal_ctx.state.recent_searches[-10:]) or "（无）",
        )
        topics = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
        if not topics:
            raise StrategistError("brainstorm returned empty topic list")
        return topics
