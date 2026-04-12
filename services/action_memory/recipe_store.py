"""
只读 recipe 存储 + 试运行管理。

加载两个目录：
- data/action_recipes/          正式 recipe（enabled=true）
- data/action_recipe_candidates/ 候选 recipe（trial=true，等待验证）

匹配优先级：正式 > 试运行。
试运行成功达到阈值后自动提升为正式 recipe。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import utils.logger as logger
from agents.base import SubTask
from services.action_memory.recipe import ActionRecipe, RecipeStep
from services.vision import PageContext


class RecipeStore:
    def __init__(
        self,
        root: str | Path = "data/action_recipes",
        candidate_root: str | Path = "data/action_recipe_candidates",
        promote_after: int = 2,
        disable_after: int = 3,
    ):
        self._root = Path(root)
        self._candidate_root = Path(candidate_root)
        self._promote_after = promote_after
        self._disable_after = disable_after
        self._recipes: list[ActionRecipe] = []
        self.reload()

    @property
    def has_recipes(self) -> bool:
        return any(r.can_run for r in self._recipes)

    def reload(self) -> None:
        self._recipes = []
        self._load_dir(self._root, trial=False)
        self._load_dir(self._candidate_root, trial=True)

    def match(self, subtask: SubTask, page_context: PageContext) -> ActionRecipe | None:
        promoted: list[ActionRecipe] = []
        trials: list[ActionRecipe] = []

        for recipe in self._recipes:
            if not self._matches(recipe, subtask, page_context):
                continue
            if recipe.trial:
                trials.append(recipe)
            else:
                promoted.append(recipe)

        # 优先返回正式 recipe
        if promoted:
            return max(promoted, key=lambda r: r.confidence)
        # 无正式命中时尝试试运行候选
        if trials:
            return max(trials, key=lambda r: r.confidence)
        return None

    def record_trial_result(self, recipe: ActionRecipe, success: bool) -> ActionRecipe:
        """记录试运行结果，达到阈值时自动提升或禁用。返回更新后的 recipe。"""
        if not recipe.trial or not recipe.source_path:
            return recipe

        if success:
            updated = replace(recipe, trial_successes=recipe.trial_successes + 1)
            if updated.trial_successes >= self._promote_after:
                return self._promote(updated)
        else:
            updated = replace(recipe, trial_failures=recipe.trial_failures + 1)
            if updated.trial_failures >= self._disable_after:
                updated = replace(updated, trial=False, enabled=False)
                logger.info(
                    {
                        "msg": "试运行 recipe 失败次数过多，已禁用",
                        "recipe_id": updated.id,
                        "failures": updated.trial_failures,
                    }
                )

        self._write_back(updated)
        self._refresh_recipe(updated)
        return updated

    def _promote(self, recipe: ActionRecipe) -> ActionRecipe:
        """将候选提升为正式 recipe：enabled=true, trial=false，移动到正式目录。"""
        promoted = replace(recipe, trial=False, enabled=True)

        source = Path(recipe.source_path)
        if source.exists():
            dest = self._root / f"{recipe.id}.json"
            self._root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))
            promoted = replace(promoted, source_path=str(dest))
            logger.info(
                {
                    "msg": "试运行 recipe 已提升为正式",
                    "recipe_id": promoted.id,
                    "successes": promoted.trial_successes,
                    "dest": str(dest),
                }
            )
        else:
            self._write_back(promoted)
            logger.info(
                {
                    "msg": "试运行 recipe 已提升（源文件不存在，原地更新）",
                    "recipe_id": promoted.id,
                }
            )

        self._refresh_recipe(promoted)
        return promoted

    def _matches(self, recipe: ActionRecipe, subtask: SubTask, page_context: PageContext) -> bool:
        if not recipe.can_run:
            return False
        if recipe.page_state not in ("any", page_context.page_state):
            return False
        if recipe.required_skill and recipe.required_skill != subtask.required_skill:
            return False

        # 优先使用 intent 精确匹配（Planner 和 Summarizer 共享同一 intent 词表）
        if recipe.intent and subtask.intent and subtask.intent != "unknown":
            return recipe.intent == subtask.intent

        # 兜底：关键词匹配（兼容无 intent 的旧 recipe）
        if recipe.match_keywords:
            return all(keyword in subtask.goal for keyword in recipe.match_keywords)
        return recipe.intent in subtask.goal

    def _load_dir(self, root: Path, *, trial: bool) -> None:
        if not root.exists():
            return
        for path in sorted(root.glob("**/*.json")):
            try:
                self._recipes.append(self._parse_recipe(path, trial_default=trial))
            except Exception as e:
                logger.warning({"msg": "动作 recipe 加载失败", "path": str(path), "error": str(e)})

    def _write_back(self, recipe: ActionRecipe) -> None:
        """将 recipe 当前状态回写到磁盘。"""
        if not recipe.source_path:
            return
        path = Path(recipe.source_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _recipe_to_dict(recipe)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _refresh_recipe(self, updated: ActionRecipe) -> None:
        """更新内存中的 recipe 列表。"""
        self._recipes = [updated if r.id == updated.id else r for r in self._recipes]

    @staticmethod
    def _parse_recipe(path: Path, *, trial_default: bool) -> ActionRecipe:
        data = json.loads(path.read_text(encoding="utf-8"))
        steps = [
            RecipeStep(
                method=step["method"],
                params=step.get("params") or {},
                delay=step.get("delay"),
                locator=step.get("locator"),
            )
            for step in data.get("steps", [])
        ]
        return ActionRecipe(
            id=str(data.get("id") or path.stem),
            intent=str(data.get("intent", "")),
            page_state=str(data.get("page_state", "any")),
            steps=steps,
            enabled=bool(data.get("enabled", not trial_default)),
            confidence=float(data.get("confidence", 1.0)),
            min_confidence=float(data.get("min_confidence", 0.8)),
            required_skill=data.get("required_skill"),
            match_keywords=list(data.get("match_keywords") or []),
            summary=str(data.get("summary", "")),
            trial=bool(data.get("trial", trial_default)),
            trial_successes=int(data.get("trial_successes", 0)),
            trial_failures=int(data.get("trial_failures", 0)),
            source_path=str(path),
        )


def _recipe_to_dict(recipe: ActionRecipe) -> dict[str, Any]:
    return {
        "id": recipe.id,
        "intent": recipe.intent,
        "page_state": recipe.page_state,
        "enabled": recipe.enabled,
        "confidence": recipe.confidence,
        "min_confidence": recipe.min_confidence,
        "required_skill": recipe.required_skill,
        "match_keywords": recipe.match_keywords,
        "summary": recipe.summary,
        "trial": recipe.trial,
        "trial_successes": recipe.trial_successes,
        "trial_failures": recipe.trial_failures,
        "steps": [
            {
                "method": step.method,
                "params": step.params,
                **({"delay": step.delay} if step.delay is not None else {}),
                **({"locator": step.locator} if step.locator is not None else {}),
            }
            for step in recipe.steps
        ],
    }
