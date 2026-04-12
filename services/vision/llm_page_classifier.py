"""LLM 页面分类器：结合本地 landmark 匹配与 LLM 视觉识别，支持页面记忆增长。"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import config
import utils.logger as logger
from agents.base import Observation
from services.vision.page_classifier import PageClassification, PageLandmark, normalize_page_state
from services.vision.page_matcher import LocalPageMatcher
from services.vision.page_registry import PageRegistry
from utils.json_utils import JsonExtractionError
from utils.prompt_template import load_prompt_template

if TYPE_CHECKING:
    from tools.llm_tool import LlmTool


_DEFAULT_PAGE_STATES = [
    "rednote_home",
    "rednote_search_results",
    "rednote_filter_panel",
    "rednote_note_detail",
    "rednote_login",
    "rednote_publish_home",
    "rednote_publish_editor",
    "unknown",
]


class LlmPageClassifier:
    """Classify new page screenshots with an LLM and persist successful labels."""

    def __init__(
        self,
        llm: LlmTool,
        registry: PageRegistry | None = None,
        *,
        model: str | None = None,
        client_name: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        enabled: bool | None = None,
        record_min_confidence: float | None = None,
        local_matcher: LocalPageMatcher | None = None,
        local_match_enabled: bool | None = None,
        local_match_min_score: float | None = None,
    ):
        cfg = config.agent.get("page_classifier", {})
        operator_cfg = config.agent["operator"]
        self._llm = llm
        self._registry = registry or PageRegistry(cfg.get("registry_file", "data/page_registry/pages.json"))
        self._model = model or cfg.get("model") or operator_cfg["model"]
        self._client = client_name or cfg.get("llm_client") or operator_cfg["llm_client"]
        self._temperature = temperature if temperature is not None else float(cfg.get("temperature", 0.0))
        self._max_tokens = max_tokens if max_tokens is not None else int(cfg.get("max_tokens", 512))
        self._enabled = enabled if enabled is not None else bool(cfg.get("enabled", True))
        self._record_min_confidence = (
            record_min_confidence
            if record_min_confidence is not None
            else float(cfg.get("record_min_confidence", 0.72))
        )
        self._local_match_enabled = (
            local_match_enabled if local_match_enabled is not None else bool(cfg.get("local_match_enabled", True))
        )
        self._local_match_min_score = (
            local_match_min_score
            if local_match_min_score is not None
            else float(cfg.get("local_match_min_score", 0.75))
        )
        self._local_matcher = local_matcher or LocalPageMatcher(
            registry=self._registry,
            min_score=self._local_match_min_score,
        )

    def classify(self, observation: Observation, trace_id: str = "system") -> PageClassification:
        if not self._enabled:
            return PageClassification(page_state="unknown", confidence=0.0, evidence=["llm_page_classifier_disabled"])

        local_match = self._classify_with_local_matcher(observation, trace_id=trace_id)
        if local_match is not None:
            return local_match

        if not self._has_api_key():
            return PageClassification(page_state="unknown", confidence=0.0, evidence=["llm_page_classifier_no_api_key"])

        try:
            classification = self._classify_with_llm(observation, trace_id=trace_id)
        except Exception as e:
            logger.warning({"msg": "LLM 页面分类失败，回退 unknown", "error": str(e)}, trace_id)
            return PageClassification(page_state="unknown", confidence=0.0, evidence=["llm_classifier_failed"])

        classification = replace(
            classification,
            page_state=normalize_page_state(classification.page_state),
            is_new_page=not self._registry.has_page_state(classification.page_state),
        )

        logger.info(
            {
                "msg": "LLM 页面分类完成",
                "page_state": classification.page_state,
                "confidence": classification.confidence,
                "is_new_page": classification.is_new_page,
            },
            trace_id,
        )

        if classification.confidence >= self._record_min_confidence and classification.page_state != "unknown":
            self._registry.record(
                classification,
                screenshot_hash=_observation_hash(observation),
                observation=observation,
            )
            logger.info(
                {
                    "msg": "LLM 页面分类已记录",
                    "page_state": classification.page_state,
                    "confidence": classification.confidence,
                    "is_new_page": classification.is_new_page,
                },
                trace_id,
            )
        else:
            logger.info(
                {
                    "msg": "LLM 页面分类未记录",
                    "page_state": classification.page_state,
                    "confidence": classification.confidence,
                    "min_confidence": self._record_min_confidence,
                    "reason": "unknown_or_low_confidence",
                },
                trace_id,
            )

        return classification

    def _classify_with_local_matcher(self, observation: Observation, trace_id: str) -> PageClassification | None:
        if not self._local_match_enabled:
            return None
        try:
            match = self._local_matcher.match(observation)
        except Exception as e:
            logger.warning({"msg": "本地 landmark 页面匹配失败，回退 LLM", "error": str(e)}, trace_id)
            return None
        if match is None:
            return None
        logger.info(
            {
                "msg": "本地 landmark 页面匹配命中",
                "page_state": match.page_state,
                "confidence": match.confidence,
            },
            trace_id,
        )
        return match

    def _classify_with_llm(self, observation: Observation, trace_id: str) -> PageClassification:
        known_pages = self._registry.summaries()
        prompt = _build_prompt(known_pages)
        image_url = f"data:image/jpeg;base64,{observation.image_base64}"
        logger.info(
            {
                "msg": "开始 LLM 页面分类",
                "known_page_count": len(known_pages),
                "max_tokens": self._max_tokens,
            },
            trace_id,
        )
        try:
            parsed = self._call_json_prompt(
                prompt=prompt,
                image_url=image_url,
                max_tokens=self._max_tokens,
                trace_id=trace_id,
            )
        except Exception as e:
            recovered = _parse_partial_classification(e.raw_text) if isinstance(e, JsonExtractionError) else None
            if recovered is not None:
                logger.warning(
                    {
                        "msg": "LLM 页面分类 JSON 解析失败，已从局部内容恢复核心分类",
                        "error": str(e),
                        "page_state": recovered.page_state,
                        "confidence": recovered.confidence,
                    },
                    trace_id,
                )
                return recovered

            logger.warning(
                {
                    "msg": "LLM 页面分类 JSON 解析失败，改用简化分类",
                    "error": str(e),
                },
                trace_id,
            )
            parsed = self._call_json_prompt(
                prompt=_build_minimal_prompt(known_pages),
                image_url=image_url,
                max_tokens=min(self._max_tokens, 768),
                trace_id=trace_id,
            )
        return _parse_classification(parsed)

    def _call_json_prompt(
        self,
        *,
        prompt: str,
        image_url: str,
        max_tokens: int,
        trace_id: str,
    ) -> Any:
        parsed, _raw = self._llm.call_json(
            messages=[
                {"role": "system", "content": "你是 GUI 页面分类器，只输出 JSON。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            model=self._model,
            client_name=self._client,
            temperature=self._temperature,
            max_tokens=max_tokens,
            trace_id=trace_id,
        )
        return parsed

    def _has_api_key(self) -> bool:
        client_config = config.model["clients"].get(self._client) or {}
        return bool(client_config.get("api_key"))


def _build_prompt(known_pages: list[dict[str, Any]]) -> str:
    known_pages_text = json.dumps(known_pages, ensure_ascii=False, indent=2)
    default_pages_text = ", ".join(_DEFAULT_PAGE_STATES)
    _meta, body = load_prompt_template("prompts/vision/page_classifier.md")
    return (
        body.replace("@@KNOWN_PAGES@@", known_pages_text if known_pages else "[]")
        .replace("@@DEFAULT_PAGE_STATES@@", default_pages_text)
        .strip()
    )


def _build_minimal_prompt(known_pages: list[dict[str, Any]]) -> str:
    known_pages_text = json.dumps(known_pages, ensure_ascii=False, separators=(",", ":"))
    default_pages_text = ", ".join(_DEFAULT_PAGE_STATES)
    return f"""
根据截图判断当前页面类型。只输出完整 JSON，不要 Markdown，不要解释。

已知页面库：{known_pages_text if known_pages else "[]"}
候选 page_state：{default_pages_text}

字段：
{{
  "page_state": "rednote_search_results",
  "confidence": 0.86,
  "is_new_page": true,
  "description": "一句中文页面用途",
  "layout_type": "feed_grid",
  "evidence": ["1-3条视觉依据"]
}}
""".strip()


def _parse_classification(parsed: Any) -> PageClassification:
    if not isinstance(parsed, dict):
        return PageClassification(page_state="unknown", confidence=0.0, evidence=["llm_returned_non_object"])

    page_state = normalize_page_state(str(parsed.get("page_state") or "unknown"))
    confidence = _clamp_float(parsed.get("confidence"), default=0.0)
    evidence = parsed.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]

    return PageClassification(
        page_state=page_state,
        confidence=confidence,
        is_new_page=bool(parsed.get("is_new_page", False)),
        description=str(parsed.get("description") or ""),
        layout_type=str(parsed.get("layout_type") or ""),
        evidence=[str(item) for item in evidence[:5]],
        stable_landmarks=_parse_landmarks(parsed.get("stable_landmarks")),
        dynamic_regions=_parse_regions(parsed.get("dynamic_regions")),
        negative_landmarks=_parse_landmarks(parsed.get("negative_landmarks")),
    )


def _parse_partial_classification(raw_text: str) -> PageClassification | None:
    page_state = _extract_string_field(raw_text, "page_state")
    if not page_state:
        return None

    confidence = _clamp_float(_extract_number_field(raw_text, "confidence"), default=0.0)
    evidence = _extract_string_array_field(raw_text, "evidence")
    evidence.append("partial_json_recovered")

    return PageClassification(
        page_state=normalize_page_state(page_state),
        confidence=confidence,
        is_new_page=bool(_extract_bool_field(raw_text, "is_new_page")),
        description=_extract_string_field(raw_text, "description"),
        layout_type=_extract_string_field(raw_text, "layout_type"),
        evidence=evidence[:5],
    )


def _extract_string_field(text: str, field: str) -> str:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*(["\'])(.*?)\1', text, re.DOTALL)
    if not match:
        return ""
    return match.group(2).replace('\\"', '"').replace("\\n", "\n").strip()


def _extract_number_field(text: str, field: str) -> float | None:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_bool_field(text: str, field: str) -> bool | None:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*(true|false)', text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _extract_string_array_field(text: str, field: str) -> list[str]:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if not match:
        return []
    return [
        item.replace('\\"', '"').replace("\\n", "\n").strip()
        for _quote, item in re.findall(r'(["\'])(.*?)\1', match.group(1), re.DOTALL)
        if item.strip()
    ]


def _clamp_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _parse_landmarks(value: Any) -> list[PageLandmark]:
    if not isinstance(value, list):
        return []
    landmarks: list[PageLandmark] = []
    for item in value:
        landmark = PageLandmark.from_raw(item)
        if landmark is not None:
            landmarks.append(landmark)
    return landmarks


def _parse_regions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _observation_hash(observation: Observation) -> str:
    import hashlib

    return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]
