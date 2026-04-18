"""services.vision.types: 页面识别相关的共享数据类型。

归集：
- PageLandmark / PageClassification   —— 分类器输出
- PageContext                          —— 分类后带截图原始观察的复合对象
- BBox / LocateResult                  —— locator 输出
- normalize_page_state / normalize_landmark_name —— 字符串归一化
- PageClassifier (Protocol)            —— 分类器接口
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from services.contracts import Observation


# ═══════════════════════════════════════════════════════
# 归一化辅助
# ═══════════════════════════════════════════════════════


def normalize_page_state(page_state: str) -> str:
    """把 LLM 输出的 page label 归一为稳定的 snake_case 名。"""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", page_state.strip().lower()).strip("_")
    return normalized or "unknown"


def normalize_landmark_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    if normalized:
        return normalized
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"landmark_{digest}"


# ═══════════════════════════════════════════════════════
# 分类输出
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class PageLandmark:
    """页面识别用的 landmark：text / template / icon / button 等。"""

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


# ═══════════════════════════════════════════════════════
# PageContext：分类结果 + 原始观察
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class PageContext:
    observation: Observation
    page_state: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    is_new_page: bool = False
    description: str = ""
    screenshot_hash: str = ""


# ═══════════════════════════════════════════════════════
# Locator 输出
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class BBox:
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)


@dataclass(frozen=True)
class LocateResult:
    ok: bool
    reason: str = ""
    score: float = 0.0
    bbox: BBox | None = None
    image_size: tuple[int, int] | None = None

    def center_norm(self) -> tuple[int, int] | None:
        """返回 0-1000 归一化坐标。"""
        if self.bbox is None or self.image_size is None:
            return None
        width, height = self.image_size
        cx, cy = self.bbox.center
        if width <= 0 or height <= 0:
            return None
        return round(cx / width * 1000), round(cy / height * 1000)


# ═══════════════════════════════════════════════════════
# 内部 helpers
# ═══════════════════════════════════════════════════════


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


__all__ = [
    "BBox",
    "LocateResult",
    "PageClassification",
    "PageClassifier",
    "PageContext",
    "PageLandmark",
    "normalize_landmark_name",
    "normalize_page_state",
]
