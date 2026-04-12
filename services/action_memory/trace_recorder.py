"""追加式轨迹记录器：记录每步操作供后续 recipe 挖掘使用。"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import utils.logger as logger
from agents.base import Action, ActionOutcome, Decision, Observation, SubTask
from services.action_memory.recipe import RecipeAttempt
from services.vision import PageContext


class TraceRecorder:
    def __init__(self, root: str | Path = "data/action_traces", enabled: bool = True):
        self._root = Path(root)
        self._enabled = enabled

    def record_llm_step(
        self,
        *,
        trace_id: str,
        subtask: SubTask,
        step: int,
        observation: Observation,
        decision: Decision,
        outcome: ActionOutcome,
        page_context: PageContext | None = None,
    ) -> None:
        self._append(
            trace_id,
            {
                "event": "llm_step",
                "subtask": self._subtask(subtask),
                "step": step,
                "observation": self._observation(observation),
                "page_state": page_context.page_state if page_context else None,
                "page_confidence": page_context.confidence if page_context else None,
                "page_is_new": page_context.is_new_page if page_context else None,
                "page_description": page_context.description if page_context else None,
                "thought": decision.thought,
                "actions": [self._action(action) for action in decision.actions],
                "outcome": self._outcome(outcome),
            },
        )

    def record_recipe_attempt(
        self,
        *,
        trace_id: str,
        subtask: SubTask,
        page_context: PageContext | None,
        attempt: RecipeAttempt,
    ) -> None:
        self._append(
            trace_id,
            {
                "event": "recipe_attempt",
                "subtask": self._subtask(subtask),
                "page_state": page_context.page_state if page_context else None,
                "page_confidence": page_context.confidence if page_context else None,
                "page_is_new": page_context.is_new_page if page_context else None,
                "page_description": page_context.description if page_context else None,
                "screenshot_hash": page_context.screenshot_hash if page_context else None,
                "status": attempt.status,
                "reason": attempt.reason,
                "recipe_id": attempt.recipe.id if attempt.recipe else None,
                "outcome": self._outcome(attempt.outcome) if attempt.outcome else None,
            },
        )

    def _append(self, trace_id: str, payload: dict[str, Any]) -> None:
        if not self._enabled:
            return
        try:
            os.makedirs(self._root, exist_ok=True)
            payload = {"time": datetime.now().isoformat(), "trace_id": trace_id, **payload}
            with open(self._root / f"{trace_id}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning({"msg": "动作轨迹记录失败", "error": str(e)}, trace_id)

    @staticmethod
    def _subtask(subtask: SubTask) -> dict[str, Any]:
        return {
            "id": subtask.id,
            "goal": subtask.goal,
            "required_skill": subtask.required_skill,
        }

    @staticmethod
    def _observation(observation: Observation) -> dict[str, Any]:
        return {
            "captured_at": observation.captured_at,
            "screenshot_hash": observation_hash(observation),
        }

    @staticmethod
    def _action(action: Action) -> dict[str, Any]:
        return {
            "method": action.method,
            "params": action.params,
            "delay": action.delay,
        }

    @staticmethod
    def _outcome(outcome: ActionOutcome) -> dict[str, Any]:
        return {
            "is_finish": outcome.is_finish,
            "summary": outcome.summary,
            "results": [
                {
                    "method": result.method,
                    "ok": result.ok,
                    "message": result.message,
                }
                for result in outcome.results
            ],
        }


def observation_hash(observation: Observation) -> str:
    return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]
