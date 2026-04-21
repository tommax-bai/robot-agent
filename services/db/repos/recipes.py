"""RecipeRepo: recipes 的 SQLite 存储（替换 action_recipes/ + action_recipe_candidates/）。

trial=1, enabled=0 → 候选；trial=0, enabled=1 → 正式。
提升 = UPDATE trial=0, enabled=1（不再移动文件）。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import utils.logger as logger
from services.action_memory.recipe import ActionRecipe, RecipeStep
from services.db.connection import Db


class RecipeRepo:
    def __init__(self, db: Db):
        self._db = db

    def list_all(self) -> list[ActionRecipe]:
        rows = self._db.read().execute("SELECT * FROM recipes").fetchall()
        return [self._row_to_recipe(r) for r in rows]

    def upsert(
        self,
        recipe: ActionRecipe,
        *,
        origin: tuple[str, str] | None = None,
    ) -> None:
        now = datetime.now().isoformat()
        origin_trace = origin[0] if origin else None
        origin_subtask = origin[1] if origin else None
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO recipes (
                        id, intent, page_state, required_skill, enabled, trial,
                        confidence, min_confidence, trial_successes, trial_failures,
                        match_keywords_json, summary, steps_json, extra_json,
                        origin_trace_id, origin_subtask_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        intent=excluded.intent,
                        page_state=excluded.page_state,
                        required_skill=excluded.required_skill,
                        enabled=excluded.enabled,
                        trial=excluded.trial,
                        confidence=excluded.confidence,
                        min_confidence=excluded.min_confidence,
                        trial_successes=excluded.trial_successes,
                        trial_failures=excluded.trial_failures,
                        match_keywords_json=excluded.match_keywords_json,
                        summary=excluded.summary,
                        steps_json=excluded.steps_json,
                        extra_json=excluded.extra_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        recipe.id,
                        recipe.intent,
                        recipe.page_state,
                        recipe.required_skill,
                        1 if recipe.enabled else 0,
                        1 if recipe.trial else 0,
                        recipe.confidence,
                        recipe.min_confidence,
                        recipe.trial_successes,
                        recipe.trial_failures,
                        json.dumps(recipe.match_keywords, ensure_ascii=False),
                        recipe.summary,
                        json.dumps(self._steps_to_list(recipe.steps), ensure_ascii=False),
                        "{}",
                        origin_trace,
                        origin_subtask,
                        now,
                        now,
                    ),
                )
            logger.info({"msg": "recipe 已写入 DB", "recipe_id": recipe.id, "trial": recipe.trial})
        except Exception as e:
            logger.error({"msg": "recipe upsert 失败", "recipe_id": recipe.id, "error": str(e)})
            raise

    def update_trial_stats(self, recipe: ActionRecipe) -> None:
        """只更新计数/状态字段（避免覆盖 steps）。"""
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    UPDATE recipes SET
                        enabled = ?, trial = ?,
                        trial_successes = ?, trial_failures = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        1 if recipe.enabled else 0,
                        1 if recipe.trial else 0,
                        recipe.trial_successes,
                        recipe.trial_failures,
                        datetime.now().isoformat(),
                        recipe.id,
                    ),
                )
            logger.info(
                {
                    "msg": "recipe trial 状态已更新",
                    "recipe_id": recipe.id,
                    "successes": recipe.trial_successes,
                    "failures": recipe.trial_failures,
                    "enabled": recipe.enabled,
                    "trial": recipe.trial,
                }
            )
        except Exception as e:
            logger.error({"msg": "recipe trial 更新失败", "recipe_id": recipe.id, "error": str(e)})
            raise

    def _row_to_recipe(self, row) -> ActionRecipe:
        steps_raw = json.loads(row["steps_json"] or "[]")
        steps = [
            RecipeStep(
                method=step["method"],
                params=step.get("params") or {},
                delay=step.get("delay"),
                locator=step.get("locator"),
            )
            for step in steps_raw
        ]
        return ActionRecipe(
            id=row["id"],
            intent=row["intent"],
            page_state=row["page_state"],
            steps=steps,
            enabled=bool(row["enabled"]),
            confidence=float(row["confidence"]),
            min_confidence=float(row["min_confidence"]),
            required_skill=row["required_skill"],
            match_keywords=json.loads(row["match_keywords_json"] or "[]"),
            summary=row["summary"] or "",
            trial=bool(row["trial"]),
            trial_successes=int(row["trial_successes"] or 0),
            trial_failures=int(row["trial_failures"] or 0),
            source_path=f"db://recipes/{row['id']}",
        )

    @staticmethod
    def _steps_to_list(steps: list[RecipeStep]) -> list[dict]:
        return [
            {
                "method": step.method,
                "params": step.params,
                **({"delay": step.delay} if step.delay is not None else {}),
                **({"locator": step.locator} if step.locator is not None else {}),
            }
            for step in steps
        ]
