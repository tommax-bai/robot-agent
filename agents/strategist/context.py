"""GoalContext / GoalResult / StateSnapshot：Strategist 的输入输出值对象。

心智模型：Strategist 的每次调用是"拿一次 snapshot + 算出 goal + 声明用到了什么"。
副作用（写 state）由调用方依据 GoalResult 决定，不再藏在 Strategist 内部。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.strategist.persona import Persona
    from services.knowledge import EvolutionContext


@dataclass(frozen=True)
class StateSnapshot:
    """Strategist 实际读取的 state 字段子集。入口拍一次，全程不可变。"""

    mood: str
    anxiety_keywords: tuple[str, ...]
    knowledge_topics: tuple[str, ...]
    recent_searches: tuple[str, ...]
    title_few_shots: tuple[str, ...]
    last_discovery: str

    @classmethod
    def from_state_dict(cls, state: dict) -> StateSnapshot:
        return cls(
            mood=state.get("mood", "neutral"),
            anxiety_keywords=tuple(state.get("anxiety_keywords", [])),
            knowledge_topics=tuple(state.get("knowledge_topics", [])),
            recent_searches=tuple(state.get("recent_searches", [])),
            title_few_shots=tuple(state.get("title_few_shots", [])),
            last_discovery=state.get("last_discovery", ""),
        )


@dataclass(frozen=True)
class GoalContext:
    """单次 Strategist 调用的完整输入。"""

    persona: Persona
    state: StateSnapshot
    inspiration: tuple[str, ...]
    now: datetime = field(default_factory=datetime.now)
    evolution: EvolutionContext | None = None


@dataclass(frozen=True)
class GoalResult:
    """Strategist 的输出：goal 文本 + 用到了什么（调用方据此决定副作用）。"""

    goal: str
    kind: str
    template: str
    used_topic: str | None = None
