"""页面分类基础类型：PageClassification, PageLandmark, PageClassifier 协议。

The first implementation is deliberately conservative. It gives the rest of the
system a stable extension point without pretending to understand dynamic pages
before we have reliable visual signatures.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agents.base import Observation


@dataclass(frozen=True)
class PageLandmark:
    name: str
    type: str
    description: str = ""
    region: dict[str, float] | None = None
    text: str = ""
    template: str = ""
    required: bool = False
    weight: float = 1.0
    threshold: float = 0.8

    @classmethod
    def from_raw(cls, raw: Any) -> PageLandmark | None:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        landmark_type = str(raw.get("type") or "").strip().lower()
        if not name or not landmark_type:
            return None
        return cls(
            name=normalize_landmark_name(name),
            type=landmark_type,
            description=str(raw.get("description") or ""),
            region=_normalize_region(raw.get("region")),
            text=str(raw.get("text") or ""),
            template=str(raw.get("template") or ""),
            required=bool(raw.get("required", False)),
            weight=_clamp_float(raw.get("weight"), default=1.0, min_value=0.0, max_value=1.0),
            threshold=_clamp_float(raw.get("threshold"), default=0.8, min_value=0.1, max_value=1.0),
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "region": self.region,
            "text": self.text,
            "template": self.template,
            "required": self.required,
            "weight": self.weight,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class PageClassification:
    page_state: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    is_new_page: bool = False
    description: str = ""
    layout_type: str = ""
    stable_landmarks: list[PageLandmark] = field(default_factory=list)
    dynamic_regions: list[dict[str, Any]] = field(default_factory=list)
    negative_landmarks: list[PageLandmark] = field(default_factory=list)


class PageClassifier(Protocol):
    def classify(self, observation: Observation, trace_id: str = "system") -> PageClassification: ...


class UnknownPageClassifier:
    """Safe default classifier used until visual signatures are configured."""

    def classify(self, observation: Observation, trace_id: str = "system") -> PageClassification:
        return PageClassification(page_state="unknown", confidence=0.0, evidence=[])


def normalize_page_state(page_state: str) -> str:
    """Normalize LLM page labels to stable snake_case names."""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", page_state.strip().lower()).strip("_")
    return normalized or "unknown"


def normalize_landmark_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    if normalized:
        return normalized
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"landmark_{digest}"


def _normalize_region(region: Any) -> dict[str, float] | None:
    if not isinstance(region, dict):
        return None
    keys = ("x1", "y1", "x2", "y2")
    if not all(key in region for key in keys):
        return None
    try:
        normalized = {key: float(region[key]) for key in keys}
    except (TypeError, ValueError):
        return None
    x1 = min(normalized["x1"], normalized["x2"])
    x2 = max(normalized["x1"], normalized["x2"])
    y1 = min(normalized["y1"], normalized["y2"])
    y2 = max(normalized["y1"], normalized["y2"])
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _clamp_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))
