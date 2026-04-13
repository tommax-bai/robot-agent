"""Recipe 快路径执行器：跳过 LLM，直接回放已验证的动作序列。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import AgentError, Observation, TaskResult
from services.action_memory import RecipeAttempt

if TYPE_CHECKING:
    from agents.base import SubTask
    from agents.operator.action_dispatcher import ActionDispatcher
    from runtime.context import RunContext
    from services.action_memory import RecipeStep, RecipeStore, TraceRecorder
    from services.vision import PageContext, PageContextCache, VisualLocator
    from tools.backends import ActionBackend


@dataclass(frozen=True)
class StepResolution:
    ok: bool
    params: dict | None = None
    reason: str = ""


class RecipeOperator:
    name = "recipe_operator"

    def __init__(
        self,
        dispatcher: ActionDispatcher,
        recipes: RecipeStore,
        page_cache: PageContextCache,
        locator: VisualLocator,
        backend: ActionBackend,
        recorder: TraceRecorder | None = None,
    ):
        self._dispatcher = dispatcher
        self._recipes = recipes
        self._page_cache = page_cache
        self._locator = locator
        self._backend = backend
        self._recorder = recorder

    @property
    def has_recipes(self) -> bool:
        return self._recipes.has_recipes

    async def try_run(self, subtask: SubTask, ctx: RunContext) -> RecipeAttempt:
        if not self._recipes.has_recipes:
            return RecipeAttempt.miss("no recipes loaded")

        image_b64, _, _ = self._backend.screenshot(ctx.trace_id, include_cursor=True)
        from datetime import datetime

        observation = Observation(image_base64=image_b64, captured_at=datetime.now().isoformat())
        page_context = self._page_cache.get_or_classify(ctx.trace_id, observation)
        recipe = self._recipes.match(subtask, page_context)
        if recipe is None:
            attempt = RecipeAttempt.miss("no matching recipe")
            self._record(ctx.trace_id, subtask, page_context, attempt)
            return attempt

        mode = "试运行" if recipe.trial else "快路径"
        logger.info(
            {
                "msg": f"命中动作 recipe，尝试{mode}执行",
                "recipe_id": recipe.id,
                "intent": recipe.intent,
                "trial": recipe.trial,
                "page_state": page_context.page_state,
            },
            ctx.trace_id,
        )

        actions = []
        for step in recipe.steps:
            resolved = self._resolve_step(step, page_context)
            if not resolved.ok:
                if recipe.trial:
                    self._recipes.record_trial_result(recipe, False)
                attempt = RecipeAttempt.unsafe(resolved.reason, recipe)
                self._record(ctx.trace_id, subtask, page_context, attempt)
                return attempt
            actions.append(step.to_action(resolved.params))

        outcome = await self._dispatcher.dispatch(actions, ctx)
        self._page_cache.invalidate(ctx.trace_id)

        success = all(result.ok for result in outcome.results)
        if success:
            attempt = RecipeAttempt.success(recipe, outcome)
        else:
            attempt = RecipeAttempt.failed("recipe action failed", recipe, outcome)

        # 试运行 recipe：记录结果，可能触发自动提升或禁用
        if recipe.trial:
            updated = self._recipes.record_trial_result(recipe, success)
            if updated.enabled and not updated.trial:
                logger.info(
                    {"msg": "试运行 recipe 已自动提升为正式", "recipe_id": updated.id},
                    ctx.trace_id,
                )

        self._record(ctx.trace_id, subtask, page_context, attempt)
        return attempt

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        attempt = await self.try_run(subtask, ctx)
        if attempt.ok and attempt.recipe is not None:
            return TaskResult.success(attempt.recipe.summary or f"recipe {attempt.recipe.id} executed")
        return TaskResult.failure(AgentError(attempt.reason or "recipe operator did not complete"))

    def _record(self, trace_id: str, subtask: SubTask, page_context, attempt: RecipeAttempt) -> None:
        if self._recorder is None:
            return
        self._recorder.record_recipe_attempt(
            trace_id=trace_id,
            subtask=subtask,
            page_context=page_context,
            attempt=attempt,
        )

    def _resolve_step(self, step: RecipeStep, page_context: PageContext) -> StepResolution:
        if not step.locator:
            return StepResolution(ok=True, params={})

        located = self._locator.locate(page_context.observation, step.locator)
        if not located.ok:
            return StepResolution(ok=False, reason=f"locator failed: {located.reason}")

        center = located.center_norm()
        if center is None:
            return StepResolution(ok=False, reason="locator did not produce center")

        x, y = center
        return StepResolution(
            ok=True,
            params={
                "x": x,
                "y": y,
                "description": step.params.get("description", step.locator.get("description", "recipe locator target")),
            },
        )
