"""本地 landmark 匹配器：基于模板匹配快速识别已知页面状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.base import Observation
from services.vision.locator import VisualLocator
from services.vision.page_classifier import PageClassification, PageLandmark
from services.vision.page_registry import PageRecord, PageRegistry

_SCORABLE_LANDMARK_TYPES = {"template", "icon", "button"}


@dataclass(frozen=True)
class LandmarkMatchResult:
    ok: bool
    page_state: str = "unknown"
    score: float = 0.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class LocalPageMatcher:
    """Match pages locally using LLM-discovered stable visual landmarks."""

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
        positive_landmarks = [landmark for landmark in record.stable_landmarks if _is_scorable(landmark)]
        if not positive_landmarks:
            return LandmarkMatchResult(
                ok=False,
                page_state=record.page_state,
                evidence=["no_scorable_landmarks"],
            )

        matched: list[str] = []
        missing: list[str] = []
        evidence: list[str] = []
        weighted_score = 0.0
        total_weight = sum(max(landmark.weight, 0.01) for landmark in positive_landmarks)

        for landmark in positive_landmarks:
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

        negative_match = self._match_negative_landmark(observation, record.negative_landmarks)
        if negative_match:
            return LandmarkMatchResult(
                ok=False,
                page_state=record.page_state,
                score=0.0,
                matched=matched,
                missing=missing,
                evidence=[*evidence, f"negative_landmark:{negative_match}"],
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

    def _match_negative_landmark(self, observation: Observation, landmarks: list[PageLandmark]) -> str:
        for landmark in landmarks:
            if not _is_scorable(landmark):
                continue
            result = self._locator.locate(observation, _landmark_spec(landmark))
            if result.ok:
                return landmark.name
        return ""


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
