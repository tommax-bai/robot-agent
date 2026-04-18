"""services.vision: 页面识别子系统。

- types.py      —— 共享数据类型（PageLandmark, PageClassification, PageContext, BBox, LocateResult）
- locator.py    —— 视觉 locator（point + template）
- pages.py      —— PageRegistry 持久化
- classifier.py —— UnknownPageClassifier / LocalPageMatcher / LlmPageClassifier
- cache.py      —— PageContextCache（按 trace_id 缓存 + 可选后台分类）
"""

from __future__ import annotations

from services.vision.cache import PageContextCache
from services.vision.classifier import (
    LandmarkMatchResult,
    LlmPageClassifier,
    LocalPageMatcher,
    UnknownPageClassifier,
)
from services.vision.locator import VisualLocator
from services.vision.pages import PageRecord, PageRegistry
from services.vision.types import (
    BBox,
    LocateResult,
    PageClassification,
    PageClassifier,
    PageContext,
    PageLandmark,
    normalize_landmark_name,
    normalize_page_state,
)

__all__ = [
    "BBox",
    "LandmarkMatchResult",
    "LlmPageClassifier",
    "LocalPageMatcher",
    "LocateResult",
    "PageClassification",
    "PageClassifier",
    "PageContext",
    "PageContextCache",
    "PageLandmark",
    "PageRecord",
    "PageRegistry",
    "UnknownPageClassifier",
    "VisualLocator",
    "normalize_landmark_name",
    "normalize_page_state",
]
