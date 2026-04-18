"""services.recipes.model: Recipe / RecipeStep / RecipeAttempt 数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from services.contracts import Action, ActionOutcome

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
    """一个可复用的 GUI 动作序列。

    enabled + 置信度达标的是正式 recipe，否则走 trial 模式参与试运行。
    """

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
    source_path: str = ""  # 源文件路径，用于回写 trial 结果

    @property
    def can_run(self) -> bool:
        if self.trial:
            return bool(self.steps)
        return self.enabled and self.confidence >= self.min_confidence and bool(self.steps)


@dataclass(frozen=True)
class RecipeAttempt:
    """一次 recipe 匹配 + 执行的结果。"""

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


__all__ = ["ActionRecipe", "RecipeAttempt", "RecipeAttemptStatus", "RecipeStep"]
