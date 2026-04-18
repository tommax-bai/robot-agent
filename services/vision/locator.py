"""services.vision.locator: 视觉模板定位。

两种 locator：
- `point`    —— 直接按 0-1000 归一化坐标定位，无匹配开销
- `template` —— OpenCV 模板匹配（optional dep）。阈值不达标返回 miss，调用方
                 应走 LLM 回退路径

cv2/numpy 是可选依赖：缺失时 `template` 路径返回 `ok=False`，不抛。
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from services.contracts import Observation
from services.vision.types import BBox, LocateResult


class VisualLocator:
    def __init__(self, template_root: str | Path = "data/vision_templates"):
        self._template_root = Path(template_root)

    def locate(self, observation: Observation, spec: dict[str, Any]) -> LocateResult:
        locator_type = spec.get("type")
        if locator_type == "point":
            return self._locate_point(observation, spec)
        if locator_type == "template":
            return self._locate_template(observation, spec)
        return LocateResult(ok=False, reason=f"unsupported locator type: {locator_type}")

    def _locate_point(self, observation: Observation, spec: dict[str, Any]) -> LocateResult:
        image = _decode(observation)
        width, height = image.size
        x = spec.get("x")
        y = spec.get("y")
        if x is None or y is None:
            return LocateResult(ok=False, reason="point locator requires x/y")
        px = int(float(x) / 1000 * width)
        py = int(float(y) / 1000 * height)
        return LocateResult(ok=True, score=1.0, bbox=BBox(px, py, px, py), image_size=(width, height))

    def _locate_template(self, observation: Observation, spec: dict[str, Any]) -> LocateResult:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except Exception:
            return LocateResult(ok=False, reason="cv2/numpy unavailable")

        template_path = self._resolve_template_path(str(spec.get("template", "")))
        if not template_path.exists():
            return LocateResult(ok=False, reason=f"template not found: {template_path}")

        image = _decode(observation).convert("RGB")
        width, height = image.size
        region = _region_to_pixels(spec.get("region"), width, height)
        cropped = image.crop((region.x1, region.y1, region.x2, region.y2))

        source = cv2.cvtColor(np.array(cropped), cv2.COLOR_RGB2GRAY)
        template_img = Image.open(template_path).convert("RGB")
        template = cv2.cvtColor(np.array(template_img), cv2.COLOR_RGB2GRAY)

        if source.shape[0] < template.shape[0] or source.shape[1] < template.shape[1]:
            return LocateResult(ok=False, reason="template larger than search region")

        if float(np.std(template)) < 1.0:
            # 纯色模板：改用 SQDIFF 避免除零
            result = cv2.matchTemplate(source, template, cv2.TM_SQDIFF)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            max_diff = 255.0 * 255.0 * float(template.size)
            score = max(0.0, 1.0 - (float(min_val) / max_diff))
            match_loc = min_loc
        else:
            result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            score = float(max_val)
            match_loc = max_loc

        threshold = float(spec.get("threshold", 0.8))
        if score < threshold:
            return LocateResult(ok=False, reason="template score below threshold", score=score)

        x1 = region.x1 + int(match_loc[0])
        y1 = region.y1 + int(match_loc[1])
        x2 = x1 + int(template.shape[1])
        y2 = y1 + int(template.shape[0])
        return LocateResult(ok=True, score=score, bbox=BBox(x1, y1, x2, y2), image_size=(width, height))

    def _resolve_template_path(self, template: str) -> Path:
        path = Path(template)
        if path.is_absolute():
            return path
        return self._template_root / template


def _decode(observation: Observation) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(observation.image_base64)))


def _region_to_pixels(region: dict | None, width: int, height: int) -> BBox:
    if not region:
        return BBox(0, 0, width, height)

    if {"x1", "y1", "x2", "y2"}.issubset(region):
        x1 = float(region["x1"])
        y1 = float(region["y1"])
        x2 = float(region["x2"])
        y2 = float(region["y2"])
    else:
        x = float(region.get("x", 0.0))
        y = float(region.get("y", 0.0))
        w = float(region.get("w", 1.0))
        h = float(region.get("h", 1.0))
        x1, y1, x2, y2 = x, y, x + w, y + h

    if max(x1, y1, x2, y2) <= 1.0:
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height

    return BBox(
        x1=max(0, min(width, int(x1))),
        y1=max(0, min(height, int(y1))),
        x2=max(0, min(width, int(x2))),
        y2=max(0, min(height, int(y2))),
    )


__all__ = ["VisualLocator"]
