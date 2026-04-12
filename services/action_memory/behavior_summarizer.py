"""LLM 旁路行为总结器：将成功的 GUI 操作轨迹提炼为可复用的 recipe 候选。"""

from __future__ import annotations

import json
import os
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import config
import utils.logger as logger
from services.vision.page_classifier import normalize_page_state
from utils.prompt_template import load_prompt_template

if TYPE_CHECKING:
    from services.intent_registry import IntentRegistry
    from tools.llm_tool import LlmTool


_ACTION_METHODS_WITH_POINT = {"click", "dblclick", "move"}
_UNREUSABLE_METHODS = {"finish"}


class BehaviorSummarizer:
    """Summarize successful LLM traces into disabled reusable recipe candidates."""

    def __init__(
        self,
        llm: LlmTool,
        intents: IntentRegistry | None = None,
        *,
        enabled: bool | None = None,
        background: bool | None = None,
        max_workers: int | None = None,
        model: str | None = None,
        client_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        trace_root: str | Path | None = None,
        candidate_root: str | Path | None = None,
        prompt_path: str | Path = "prompts/action_memory/behavior_summarizer.md",
        max_events: int | None = None,
        auto_enable: bool | None = None,
        auto_enable_min_confidence: float | None = None,
    ):
        cfg = config.agent.get("behavior_summarizer", {})
        operator_cfg = config.agent["operator"]
        self._llm = llm
        self._intents = intents
        self._enabled = enabled if enabled is not None else bool(cfg.get("enabled", True))
        self._background = background if background is not None else bool(cfg.get("background_enabled", True))
        self._model = model or cfg.get("model") or operator_cfg["model"]
        self._client = client_name or cfg.get("llm_client") or operator_cfg["llm_client"]
        self._temperature = temperature if temperature is not None else float(cfg.get("temperature", 0.0))
        self._max_tokens = max_tokens if max_tokens is not None else int(cfg.get("max_tokens", 1200))
        self._trace_root = Path(trace_root or cfg.get("trace_root", "data/action_traces"))
        self._candidate_root = Path(candidate_root or cfg.get("candidate_root", "data/action_recipe_candidates"))
        self._prompt_path = Path(prompt_path)
        self._max_events = max_events if max_events is not None else int(cfg.get("max_events", 8))
        self._auto_enable = auto_enable if auto_enable is not None else bool(cfg.get("auto_enable", False))
        self._auto_enable_min_confidence = (
            auto_enable_min_confidence
            if auto_enable_min_confidence is not None
            else float(cfg.get("auto_enable_min_confidence", 0.92))
        )
        worker_count = max_workers if max_workers is not None else int(cfg.get("background_workers", 1))
        self._executor = (
            ThreadPoolExecutor(max_workers=max(1, worker_count), thread_name_prefix="behavior-summarizer")
            if self._background
            else None
        )
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def submit_trace(self, trace_id: str, subtask_id: str | None = None, subtask_intent: str = "unknown") -> None:
        if not self._enabled:
            return
        key = _summary_key(trace_id, subtask_id)
        if self._background:
            if self._executor is None:
                logger.warning({"msg": "行为总结器后台执行器未初始化"}, trace_id)
                return
            with self._lock:
                if key in self._pending:
                    return
                self._pending.add(key)
            future = self._executor.submit(self.summarize_trace, trace_id, subtask_id, subtask_intent)
            future.add_done_callback(lambda done: self._on_summary_done(key, trace_id, done))
            logger.info({"msg": "旁路行为总结已调度", "subtask_id": subtask_id}, trace_id)
            return

        try:
            self.summarize_trace(trace_id, subtask_id, subtask_intent)
        except Exception as e:
            logger.warning({"msg": "旁路行为总结失败", "error": str(e)}, trace_id)

    def summarize_trace(self, trace_id: str, subtask_id: str | None = None, subtask_intent: str = "unknown") -> list[Path]:
        if not self._enabled or not self._has_api_key(trace_id):
            return []

        events = self._load_reusable_events(trace_id, subtask_id)
        if not events:
            logger.info({"msg": "旁路行为总结跳过：没有可总结的成功动作", "subtask_id": subtask_id}, trace_id)
            return []

        subtask = events[0].get("subtask") or {}
        prompt = self._build_prompt(
            events=events,
            subtask_goal=str(subtask.get("goal") or ""),
            subtask_intent=subtask_intent,
        )
        parsed, _raw = self._llm.call_json(
            messages=[
                {"role": "system", "content": "你是 GUI 行为总结器，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            model=self._model,
            client_name=self._client,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            trace_id=trace_id,
        )
        recipe = self._normalize_recipe(
            parsed, trace_id=trace_id, subtask_id=str(subtask.get("id") or subtask_id or "")
        )
        if recipe is None:
            logger.info(
                {"msg": "旁路行为总结未生成候选", "reason": _reason(parsed), "subtask_id": subtask_id}, trace_id
            )
            return []

        path = self._write_candidate(trace_id, str(subtask.get("id") or subtask_id or "unknown_subtask"), recipe)
        return [path]

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _on_summary_done(self, key: str, trace_id: str, future: Future[list[Path]]) -> None:
        with self._lock:
            self._pending.discard(key)

        try:
            written = future.result()
        except Exception as e:
            logger.warning({"msg": "旁路行为总结失败", "error": str(e)}, trace_id)
            return
        if written:
            logger.info({"msg": "旁路行为总结完成", "candidate_count": len(written)}, trace_id)

    def _build_prompt(self, *, events: list[dict[str, Any]], subtask_goal: str, subtask_intent: str = "unknown") -> str:
        _meta, body = load_prompt_template(str(self._prompt_path))
        intent_candidates = self._intents.format_for_prompt() if self._intents else "(无可用 intent 列表)"
        trace_events = json.dumps(
            [_event_summary(event) for event in events[-self._max_events :]], ensure_ascii=False, indent=2
        )
        return (
            body.replace("@@SUBTASK_GOAL@@", subtask_goal)
            .replace("@@SUBTASK_INTENT@@", subtask_intent)
            .replace("@@INTENT_CANDIDATES@@", intent_candidates)
            .replace("@@TRACE_EVENTS@@", trace_events)
            .strip()
        )

    def _load_reusable_events(self, trace_id: str, subtask_id: str | None) -> list[dict[str, Any]]:
        path = self._trace_root / f"{trace_id}.jsonl"
        if not path.exists():
            return []

        events: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") != "llm_step":
                    continue
                subtask = event.get("subtask") or {}
                if subtask_id and str(subtask.get("id") or "") != subtask_id:
                    continue
                if not _outcome_ok(event.get("outcome") or {}):
                    continue
                actions = [
                    action for action in event.get("actions") or [] if action.get("method") not in _UNREUSABLE_METHODS
                ]
                if not actions:
                    continue
                event = dict(event)
                event["actions"] = actions
                events.append(event)

        return events[-self._max_events :]

    def _normalize_recipe(self, parsed: Any, *, trace_id: str, subtask_id: str) -> dict[str, Any] | None:
        if not isinstance(parsed, dict) or not bool(parsed.get("reusable")):
            return None
        raw = parsed.get("recipe")
        if not isinstance(raw, dict):
            return None

        steps = _normalize_steps(raw.get("steps"))
        if not steps:
            return None

        confidence = _clamp_float(raw.get("confidence"), default=0.5, min_value=0.0, max_value=1.0)
        page_state = normalize_page_state(str(raw.get("page_state") or "any"))
        recipe_id = _safe_id(str(raw.get("id") or f"candidate_{trace_id}_{subtask_id}"))
        raw_intent = str(raw.get("intent") or raw.get("summary") or subtask_id or recipe_id)
        intent = self._intents.resolve(raw_intent) if self._intents else raw_intent
        return {
            "id": recipe_id,
            "intent": intent,
            "page_state": page_state,
            "enabled": False,
            "trial": True,
            "trial_successes": 0,
            "trial_failures": 0,
            "confidence": confidence,
            "min_confidence": _clamp_float(raw.get("min_confidence"), default=0.8, min_value=0.0, max_value=1.0),
            "required_skill": raw.get("required_skill"),
            "match_keywords": _string_list(raw.get("match_keywords"), limit=8),
            "summary": str(raw.get("summary") or ""),
            "preconditions": _string_list(raw.get("preconditions"), limit=8),
            "expected": raw.get("expected") if isinstance(raw.get("expected"), dict) else {},
            "source": {
                "trace_id": trace_id,
                "subtask_id": subtask_id,
                "mined_at": datetime.now().isoformat(),
                "miner": "llm_behavior_summarizer",
                "reason": str(parsed.get("reason") or ""),
            },
            "steps": steps,
        }

    def _write_candidate(self, trace_id: str, subtask_id: str, recipe: dict[str, Any]) -> Path:
        os.makedirs(self._candidate_root / trace_id, exist_ok=True)
        path = self._candidate_root / trace_id / f"{_safe_id(subtask_id)}_llm.json"
        path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info({"msg": "生成 LLM 候选动作 recipe", "path": str(path), "recipe_id": recipe["id"]}, trace_id)
        return path

    def _has_api_key(self, trace_id: str) -> bool:
        client_config = config.model["clients"].get(self._client) or {}
        if not client_config.get("api_key"):
            logger.info({"msg": "旁路行为总结跳过：LLM API key 未配置", "client": self._client}, trace_id)
            return False
        return True


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    subtask = event.get("subtask") or {}
    return {
        "step": event.get("step"),
        "subtask_id": subtask.get("id"),
        "subtask_goal": _truncate(str(subtask.get("goal") or ""), 180),
        "required_skill": subtask.get("required_skill"),
        "page_state": event.get("page_state"),
        "page_confidence": event.get("page_confidence"),
        "page_description": _truncate(str(event.get("page_description") or ""), 120),
        "thought": _truncate(str(event.get("thought") or ""), 260),
        "actions": [_action_summary(action) for action in event.get("actions") or []],
        "outcome": event.get("outcome"),
    }


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": action.get("method"),
        "params": action.get("params") or {},
        "delay": action.get("delay"),
    }


def _normalize_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    steps: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        method = str(raw.get("method") or "").strip().lower()
        if not method or method in _UNREUSABLE_METHODS:
            continue

        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        params = dict(params)
        locator = raw.get("locator") if isinstance(raw.get("locator"), dict) else None
        if locator is None and method in _ACTION_METHODS_WITH_POINT:
            locator = _point_locator_from_params(params)
        if method in _ACTION_METHODS_WITH_POINT and locator is None:
            continue

        step = {
            "method": method,
            "params": params,
        }
        if locator:
            step["locator"] = locator
        if raw.get("delay") is not None:
            step["delay"] = raw.get("delay")
        steps.append(step)

    return steps


def _point_locator_from_params(params: dict[str, Any]) -> dict[str, Any] | None:
    x = _int_or_none(params.get("x"))
    y = _int_or_none(params.get("y"))
    if x is None or y is None:
        return None
    return {
        "type": "point",
        "x": x,
        "y": y,
        "description": str(params.get("description") or "历史点击位置"),
    }


def _outcome_ok(outcome: dict[str, Any]) -> bool:
    results = outcome.get("results") or []
    return bool(results) and all(bool(result.get("ok")) for result in results)


def _string_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()][:limit]


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
    return normalized or "behavior_recipe"


def _summary_key(trace_id: str, subtask_id: str | None) -> str:
    return f"{trace_id}:{subtask_id or '*'}"


def _reason(parsed: Any) -> str:
    if isinstance(parsed, dict):
        return str(parsed.get("reason") or "not_reusable")
    return "invalid_summary_response"


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(1000, parsed))


def _clamp_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
