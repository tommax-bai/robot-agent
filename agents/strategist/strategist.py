"""Strategist: 目标生成器的注册表 Facade。

与旧实现的关键差异：
- 不再挂"动词方法"（brainstorm / generate_patrol_goal / generate_posting_goal），
  而是按 kind 派发到 GoalGenerator。加 goal 类型 = 加文件 + 注册，旧代码零改动。
- 不持有 state：调用方先 state.get() 拍快照构造 GoalContext 传入。
- 不写 state：所有副作用（比如 recent_searches）从 GoalResult 返回，
  调用方决定是否应用。
- 输出 typed GoalResult（含 goal + used_topic + template 名），不再裸 str。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.strategist.brainstormer import Brainstormer
from agents.strategist.generators import (
    GoalGeneratorRegistry,
    PatrolGoalGenerator,
    PostGoalGenerator,
)
from agents.strategist.renderer import PromptRenderer
from agents.strategist.selector import RandomSelector, Selector

if TYPE_CHECKING:
    from agents.strategist.context import GoalContext, GoalResult
    from tools.llm import LlmTool


class Strategist:
    name = "strategist"

    def __init__(self, registry: GoalGeneratorRegistry):
        self._registry = registry

    async def generate(
        self,
        kind: str,
        goal_ctx: GoalContext,
        *,
        trace_id: str = "system",
    ) -> GoalResult:
        return await self._registry.get(kind).generate(goal_ctx, trace_id=trace_id)

    def kinds(self) -> list[str]:
        return self._registry.kinds()


def build_default_strategist(
    llm: LlmTool,
    *,
    prompts_dir: str = "prompts/agent",
    selector: Selector | None = None,
) -> Strategist:
    """默认 wiring：预加载所有模板，注册 patrol + post 两个 generator。"""
    renderer = PromptRenderer(llm, prompts_dir=prompts_dir)
    renderer.preload("brainstorm.md", "patrol_goal.md", "posting_goal.md")
    brainstormer = Brainstormer(renderer)
    sel = selector or RandomSelector()

    registry = GoalGeneratorRegistry()
    registry.register(PatrolGoalGenerator(brainstormer, renderer, sel))
    registry.register(PostGoalGenerator(brainstormer, renderer, sel))

    return Strategist(registry)
