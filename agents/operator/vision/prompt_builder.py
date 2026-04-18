"""VisionPromptBuilder：3 个 system prompt 模板预加载 + 缓存 + 拼接。

替代 VisionActionStep._build_system_prompt 里每个子任务 reload 3 次磁盘。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from utils.prompt import load_prompt

if TYPE_CHECKING:
    from agents.base import SubTask
    from services.skill_registry import SkillRegistry


class VisionPromptBuilder:
    def __init__(
        self,
        skills: SkillRegistry,
        action_template: str = "prompts/operator/action.md",
        common_rules_template: str = "prompts/operator/common_skill_rules.md",
        constraint_template: str = "prompts/operator/sub_goal_constraint.md",
    ):
        self._skills = skills
        self._action_template = action_template
        self._common_rules_template = common_rules_template
        self._constraint_template = constraint_template
        self._action_body: str | None = None
        self._common_rules_body: str | None = None
        self._constraint_body: str | None = None

    def preload(self) -> None:
        self._load_action()
        self._load_common_rules()
        self._load_constraint()

    def build_system_prompt(self, subtask: SubTask, *, now: datetime | None = None) -> str:
        current_time = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        action_section = self._load_action().format(CURRENT_TIME=current_time)
        common_rules_section = self._load_common_rules()
        skill_section = (
            self._skills.build_prompt([subtask.required_skill]) if subtask.required_skill else ""
        )
        constraint_section = self._load_constraint().format(sub_goal=subtask.goal)
        return "\n".join([action_section, common_rules_section, skill_section, constraint_section])

    def _load_action(self) -> str:
        if self._action_body is None:
            _, self._action_body = load_prompt(self._action_template)
        return self._action_body

    def _load_common_rules(self) -> str:
        if self._common_rules_body is None:
            _, self._common_rules_body = load_prompt(self._common_rules_template)
        return self._common_rules_body

    def _load_constraint(self) -> str:
        if self._constraint_body is None:
            _, self._constraint_body = load_prompt(self._constraint_template)
        return self._constraint_body
