"""PlanPromptBuilder：模板预加载 + 变量注入，一次加载复用。

替代原 Planner 内联的 `load_prompt + body.format()`：
模板在构造期加载，不是每次调用都重读磁盘。

时间通过参数注入而非 datetime.now() 硬编码，便于测试固定时钟。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from utils.prompt import load_prompt

if TYPE_CHECKING:
    from services.intents import IntentRegistry
    from services.skill_registry import SkillRegistry


class PlanPromptBuilder:
    def __init__(
        self,
        skills: SkillRegistry,
        intents: IntentRegistry,
        template_path: str = "prompts/operator/plan.md",
    ):
        self._skills = skills
        self._intents = intents
        self._template_path = template_path
        self._body: str | None = None

    def preload(self) -> None:
        """启动期一次性加载模板；不调用也无碍，首次 build 时会懒加载。"""
        self._load()

    def build(self, user_goal: str, *, now: datetime | None = None) -> str:
        body = self._load()
        current_time = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        skills_manifest = "".join(
            f"- {m.name}: {m.description}\n" for m in self._skills.get_all()
        )
        intent_candidates = self._intents.format_for_prompt()
        return body.format(
            SKILL_MANIFEST=skills_manifest,
            INTENT_CANDIDATES=intent_candidates,
            USER_GOAL=user_goal,
            CURRENT_TIME=current_time,
        )

    def _load(self) -> str:
        if self._body is None:
            _, self._body = load_prompt(self._template_path)
        return self._body
