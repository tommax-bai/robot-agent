"""services.vision.classifier: 页面分类器。

三种实现：
- `UnknownPageClassifier`  —— 安全默认，总是返回 unknown
- `LocalPageMatcher`       —— 基于已登记 landmark 的模板匹配，无 LLM 成本
- `LlmPageClassifier`      —— 先试 local matcher；miss 则调用 VLM，高置信度结果写回 registry

对外暴露 `PageClassifier` Protocol（定义在 services.vision.types）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import config
import utils.logger as logger
from services.contracts import Observation
from services.vision.locator import VisualLocator
from services.vision.pages import PageRecord, PageRegistry
from services.vision.types import (
    PageClassification,
    PageLandmark,
    normalize_page_state,
)
from utils.json_parse import JsonExtractionError
from utils.prompt import load_prompt

if TYPE_CHECKING:
    from tools.llm import LlmTool


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

_SCORABLE_LANDMARK_TYPES = {"template", "icon", "button"}


# ═══════════════════════════════════════════════════════
# UnknownPageClassifier
# ═══════════════════════════════════════════════════════


class UnknownPageClassifier:
    """Fallback classifier — always returns unknown. 安全兜底。"""

    def classify(self, observation: Observation, trace_id: str = "system") -> PageClassification:
        return PageClassification(page_state="unknown", confidence=0.0, evidence=[])


# ═══════════════════════════════════════════════════════
# LocalPageMatcher
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class LandmarkMatchResult:
    ok: bool
    page_state: str = "unknown"
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class LocalPageMatcher:
    """用已登记的 landmark 做模板匹配，匹配到则跳过 LLM。"""

    def __init__(
        self,
        registry: PageRegistry,
        *,
        locator: VisualLocator | None = None,
        min_score: float = 0.75,
    ):
        self._registry = registry
        self._locator = locator or VisualLocator(template_root=registry.template_root)
        self._min_score = min_score

    def match(self, observation: Observation) -> PageClassification | None:
        best_record: PageRecord | None = None
        best_match: LandmarkMatchResult | None = None
        for record in self._registry.list_records():
            result = self._match_record(observation, record)
            if not result.ok:
                continue
            if best_match is None or result.score > best_match.score:
                best_record = record
                best_match = result

        if best_record is None or best_match is None:
            return None

        return PageClassification(
            page_state=best_record.page_state,
            confidence=best_match.score,
            evidence=[
                f"local_landmark_match:{','.join(best_match.matched)}",
                *best_match.evidence,
            ],
            is_new_page=False,
            description=best_record.description,
            layout_type=best_record.layout_type,
            stable_landmarks=best_record.stable_landmarks,
            dynamic_regions=best_record.dynamic_regions,
            negative_landmarks=best_record.negative_landmarks,
        )

    def _match_record(self, observation: Observation, record: PageRecord) -> LandmarkMatchResult:
        positives = [lm for lm in record.stable_landmarks if _is_scorable(lm)]
        if not positives:
            return LandmarkMatchResult(ok=False, page_state=record.page_state, evidence=["no_scorable_landmarks"])

        matched: list[str] = []
        missing: list[str] = []
        evidence: list[str] = []
        weighted_score = 0.0
        total_weight = sum(max(lm.weight, 0.01) for lm in positives)

        for landmark in positives:
            weight = max(landmark.weight, 0.01)
            result = self._locator.locate(observation, _landmark_spec(landmark))
            if result.ok:
                matched.append(landmark.name)
                weighted_score += weight * result.score
                evidence.append(f"{landmark.name}:{result.score:.2f}")
                continue

            missing.append(landmark.name)
            if landmark.required:
                return LandmarkMatchResult(
                    ok=False,
                    page_state=record.page_state,
                    score=weighted_score / total_weight,
                    matched=matched,
                    missing=missing,
                    evidence=[*evidence, f"{landmark.name}:required_missing"],
                )

        negative_hit = self._first_negative_hit(observation, record.negative_landmarks)
        if negative_hit:
            return LandmarkMatchResult(
                ok=False,
                page_state=record.page_state,
                score=0.0,
                matched=matched,
                missing=missing,
                evidence=[*evidence, f"negative_landmark:{negative_hit}"],
            )

        score = weighted_score / total_weight
        return LandmarkMatchResult(
            ok=score >= self._min_score,
            page_state=record.page_state,
            score=score,
            matched=matched,
            missing=missing,
            evidence=evidence,
        )

    def _first_negative_hit(
        self, observation: Observation, landmarks: list[PageLandmark]
    ) -> str:
        for landmark in landmarks:
            if not _is_scorable(landmark):
                continue
            if self._locator.locate(observation, _landmark_spec(landmark)).ok:
                return landmark.name
        return ""


# ═══════════════════════════════════════════════════════
# LlmPageClassifier
# ═══════════════════════════════════════════════════════


class LlmPageClassifier:
    """先本地 landmark 匹配；miss 则调 VLM；高置信度的结果写回 registry。"""

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
        cfg = config.settings.agent.page_classifier
        op_cfg = config.settings.agent.operator

        self._llm = llm
        self._registry = registry or PageRegistry(cfg.registry_file)
        self._model = model or cfg.model or op_cfg.model
        self._client = client_name or cfg.llm_client or op_cfg.llm_client
        self._temperature = cfg.temperature if temperature is None else temperature
        self._max_tokens = cfg.max_tokens if max_tokens is None else max_tokens
        self._enabled = cfg.enabled if enabled is None else enabled
        self._record_min_confidence = (
            cfg.record_min_confidence if record_min_confidence is None else record_min_confidence
        )
        self._local_enabled = (
            cfg.local_match_enabled if local_match_enabled is None else local_match_enabled
        )
        min_score = (
            cfg.local_match_min_score if local_match_min_score is None else local_match_min_score
        )
        self._local_matcher = local_matcher or LocalPageMatcher(
            registry=self._registry, min_score=min_score
        )

    def classify(self, observation: Observation, trace_id: str = "system") -> PageClassification:
        if not self._enabled:
            return PageClassification(
                page_state="unknown", confidence=0.0, evidence=["llm_page_classifier_disabled"]
            )

        local = self._try_local(observation, trace_id)
        if local is not None:
            return local

        if not self._has_api_key():
            return PageClassification(
                page_state="unknown", confidence=0.0, evidence=["llm_page_classifier_no_api_key"]
            )

        try:
            classification = self._classify_with_llm(observation, trace_id)
        except Exception as e:
            logger.warning({"msg": "LLM 页面分类失败，回退 unknown", "error": str(e)}, trace_id)
            return PageClassification(
                page_state="unknown", confidence=0.0, evidence=["llm_classifier_failed"]
            )

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

        if (
            classification.confidence >= self._record_min_confidence
            and classification.page_state != "unknown"
        ):
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

    def _try_local(self, observation: Observation, trace_id: str) -> PageClassification | None:
        if not self._local_enabled:
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
            parsed = self._call_json(prompt, image_url, self._max_tokens, trace_id)
        except Exception as e:
            recovered = _parse_partial(e.raw_text) if isinstance(e, JsonExtractionError) else None
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
                {"msg": "LLM 页面分类 JSON 解析失败，改用简化分类", "error": str(e)},
                trace_id,
            )
            parsed = self._call_json(
                _build_minimal_prompt(known_pages),
                image_url,
                min(self._max_tokens, 768),
                trace_id,
            )
        return _parse_classification(parsed)

    def _call_json(self, prompt: str, image_url: str, max_tokens: int, trace_id: str) -> Any:
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
        client_config = config.settings.llm.clients.get(self._client)
        return bool(client_config and client_config.api_key)


# ═══════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════


def _is_scorable(landmark: PageLandmark) -> bool:
    return bool(landmark.template) and landmark.type in _SCORABLE_LANDMARK_TYPES


def _landmark_spec(landmark: PageLandmark) -> dict[str, Any]:
    return {
        "type": "template",
        "template": landmark.template,
        "threshold": landmark.threshold,
        "region": _expand_region(landmark.region),
    }


def _expand_region(region: dict[str, float] | None, padding: float = 0.04) -> dict[str, float] | None:
    if not region:
        return None
    if max(region.values()) > 1.0:
        return region
    return {
        "x1": max(0.0, region["x1"] - padding),
        "y1": max(0.0, region["y1"] - padding),
        "x2": min(1.0, region["x2"] + padding),
        "y2": min(1.0, region["y2"] + padding),
    }


def _observation_hash(observation: Observation) -> str:
    return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]


def _build_prompt(known_pages: list[dict[str, Any]]) -> str:
    known_pages_text = json.dumps(known_pages, ensure_ascii=False, indent=2)
    default_pages_text = ", ".join(_DEFAULT_PAGE_STATES)
    _meta, body = load_prompt("prompts/vision/page_classifier.md")
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
        return PageClassification(
            page_state="unknown", confidence=0.0, evidence=["llm_returned_non_object"]
        )

    evidence = parsed.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]

    return PageClassification(
        page_state=normalize_page_state(str(parsed.get("page_state") or "unknown")),
        confidence=_clamp01(parsed.get("confidence"), default=0.0),
        is_new_page=bool(parsed.get("is_new_page", False)),
        description=str(parsed.get("description") or ""),
        layout_type=str(parsed.get("layout_type") or ""),
        evidence=[str(item) for item in evidence[:5]],
        stable_landmarks=_parse_landmarks(parsed.get("stable_landmarks")),
        dynamic_regions=_parse_regions(parsed.get("dynamic_regions")),
        negative_landmarks=_parse_landmarks(parsed.get("negative_landmarks")),
    )


def _parse_partial(raw_text: str) -> PageClassification | None:
    page_state = _extract_string(raw_text, "page_state")
    if not page_state:
        return None

    evidence = _extract_string_array(raw_text, "evidence")
    evidence.append("partial_json_recovered")

    return PageClassification(
        page_state=normalize_page_state(page_state),
        confidence=_clamp01(_extract_number(raw_text, "confidence"), default=0.0),
        is_new_page=bool(_extract_bool(raw_text, "is_new_page")),
        description=_extract_string(raw_text, "description"),
        layout_type=_extract_string(raw_text, "layout_type"),
        evidence=evidence[:5],
    )


def _extract_string(text: str, field: str) -> str:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*(["\'])(.*?)\1', text, re.DOTALL)
    if not match:
        return ""
    return match.group(2).replace('\\"', '"').replace("\\n", "\n").strip()


def _extract_number(text: str, field: str) -> float | None:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_bool(text: str, field: str) -> bool | None:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*(true|false)', text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def _extract_string_array(text: str, field: str) -> list[str]:
    match = re.search(rf'["\']{re.escape(field)}["\']\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if not match:
        return []
    return [
        item.replace('\\"', '"').replace("\\n", "\n").strip()
        for _q, item in re.findall(r'(["\'])(.*?)\1', match.group(1), re.DOTALL)
        if item.strip()
    ]


def _clamp01(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _parse_landmarks(value: Any) -> list[PageLandmark]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        landmark = PageLandmark.from_raw(item)
        if landmark is not None:
            out.append(landmark)
    return out


def _parse_regions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


__all__ = [
    "LandmarkMatchResult",
    "LlmPageClassifier",
    "LocalPageMatcher",
    "UnknownPageClassifier",
]
