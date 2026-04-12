"""可复用动作 recipe 的数据结构：ActionRecipe, RecipeStep, RecipeAttempt。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agents.base import Action, ActionOutcome

RecipeAttemptStatus = Literal["success", "miss", "unsafe", "failed"]


@dataclass(frozen=True)
class RecipeStep:
    method: str
    params: dict
    delay: float | None = None
    locator: dict | None = None

    def to_action(self, resolved_params: dict | None = None) -> Action:
        params = dict(self.params)
        if resolved_params:
            params.update(resolved_params)
        return Action(method=self.method, params=params, delay=self.delay)


@dataclass(frozen=True)
class ActionRecipe:
    id: str
    intent: str
    page_state: str = "any"
    steps: list[RecipeStep] = field(default_factory=list)
    enabled: bool = True
    confidence: float = 1.0
    min_confidence: float = 0.8
    required_skill: str | None = None
    match_keywords: list[str] = field(default_factory=list)
    summary: str = ""
    trial: bool = False
    trial_successes: int = 0
    trial_failures: int = 0
    source_path: str = ""  # 文件路径，用于回写试运行结果

    @property
    def can_run(self) -> bool:
        """正式 recipe：enabled + 置信度达标。试运行 recipe：trial=True 即可参与。"""
        if self.trial:
            return bool(self.steps)
        return self.enabled and self.confidence >= self.min_confidence and bool(self.steps)


@dataclass(frozen=True)
class RecipeAttempt:
    status: RecipeAttemptStatus
    reason: str = ""
    recipe: ActionRecipe | None = None
    outcome: ActionOutcome | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"

    @classmethod
    def success(cls, recipe: ActionRecipe, outcome: ActionOutcome) -> RecipeAttempt:
        return cls(status="success", recipe=recipe, outcome=outcome)

    @classmethod
    def miss(cls, reason: str) -> RecipeAttempt:
        return cls(status="miss", reason=reason)

    @classmethod
    def unsafe(cls, reason: str, recipe: ActionRecipe | None = None) -> RecipeAttempt:
        return cls(status="unsafe", reason=reason, recipe=recipe)

    @classmethod
    def failed(cls, reason: str, recipe: ActionRecipe, outcome: ActionOutcome | None = None) -> RecipeAttempt:
        return cls(status="failed", reason=reason, recipe=recipe, outcome=outcome)
