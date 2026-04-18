"""SubTaskNormalizer：raw_task dict → SubTask。

两个 normalize 步骤职责分离：
- skill gate：LLM 输出了不在 registry 里的 skill → 降级为 None（视觉通用）
- intent resolve：LLM 输出的别名/近义词 → 归一化到 IntentRegistry 里的标准名

sub_goal 缺失视为 LLM 输出质量问题，抛 PlannerError 让 operator 知道规划失败。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import PlannerError, SubTask
from utils.events import Ev, ev

if TYPE_CHECKING:
    from services.intents import IntentRegistry
    from services.skill_registry import SkillRegistry


class SubTaskNormalizer:
    def __init__(self, skills: SkillRegistry, intents: IntentRegistry):
        self._skills = skills
        self._intents = intents

    def normalize(self, raw_tasks: list[dict], *, trace_id: str) -> list[SubTask]:
        out: list[SubTask] = []
        for i, t in enumerate(raw_tasks):
            sub_goal = t.get("sub_goal")
            if not sub_goal:
                raise PlannerError(f"子任务 {i} 缺少 sub_goal")
            skill = self._resolve_skill(t.get("required_skill"), trace_id)
            intent = self._resolve_intent(t.get("intent", ""), trace_id)
            out.append(SubTask(id=f"sub-{i}", goal=sub_goal, required_skill=skill, intent=intent))
        return out

    def _resolve_skill(self, skill: str | None, trace_id: str) -> str | None:
        if skill and self._skills.get(skill) is None:
            ev(Ev.PLAN_SKILL_INVALID, trace_id=trace_id, skill=skill)
            return None
        return skill

    def _resolve_intent(self, raw_intent: str, trace_id: str) -> str:
        resolved = self._intents.resolve(raw_intent)
        if resolved != raw_intent and raw_intent:
            ev(Ev.PLAN_INTENT_RESOLVED, trace_id=trace_id, raw=raw_intent, resolved=resolved)
        return resolved
