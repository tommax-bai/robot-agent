"""services.vision.pages: 页面注册表持久化。

`PageRegistry` 是已知 page_state 的 JSON 仓储，保存 LLM 首次识别到的 landmark
描述 + 从首张截图裁剪出来的 template 图，供后续的 local matcher 快速对比。
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

import utils.logger as logger
from services.contracts import Observation
from services.vision.types import (
    BBox,
    PageClassification,
    PageLandmark,
    normalize_page_state,
)
from utils.fs import atomic_write_text


_TEMPLATE_LANDMARK_TYPES = {"template", "icon", "button"}


@dataclass(frozen=True)
class PageRecord:
    page_state: str
    description: str = ""
    layout_type: str = ""
    evidence: list[str] = field(default_factory=list)
    stable_landmarks: list[PageLandmark] = field(default_factory=list)
    dynamic_regions: list[dict[str, Any]] = field(default_factory=list)
    negative_landmarks: list[PageLandmark] = field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    seen_count: int = 0
    screenshot_hashes: list[str] = field(default_factory=list)


class PageRegistry:
    def __init__(
        self,
        path: str | Path = "data/page_registry/pages.json",
        max_hashes_per_page: int = 20,
    ):
        self._path = Path(path)
        self._max_hashes = max_hashes_per_page

    @property
    def template_root(self) -> Path:
        return self._path.parent

    def list_records(self) -> list[PageRecord]:
        return list(self._read().values())

    def has_page_state(self, page_state: str) -> bool:
        return normalize_page_state(page_state) in self._read()

    def summaries(self) -> list[dict[str, Any]]:
        return [_record_summary(record) for record in self.list_records()]

    def record(
        self,
        classification: PageClassification,
        *,
        screenshot_hash: str,
        observation: Observation | None = None,
    ) -> PageRecord | None:
        page_state = normalize_page_state(classification.page_state)
        if page_state == "unknown":
            return None

        pages = self._read()
        existing = pages.get(page_state)
        stable_landmarks = self._materialize_templates(
            page_state=page_state,
            observation=observation,
            landmarks=classification.stable_landmarks,
            existing_landmarks=existing.stable_landmarks if existing else [],
        )

        now = datetime.now().isoformat()
        if existing is None:
            record = PageRecord(
                page_state=page_state,
                description=classification.description,
                layout_type=classification.layout_type,
                evidence=classification.evidence,
                stable_landmarks=stable_landmarks,
                dynamic_regions=classification.dynamic_regions,
                negative_landmarks=classification.negative_landmarks,
                first_seen_at=now,
                last_seen_at=now,
                seen_count=1,
                screenshot_hashes=[screenshot_hash],
            )
        else:
            record = PageRecord(
                page_state=page_state,
                description=classification.description or existing.description,
                layout_type=classification.layout_type or existing.layout_type,
                evidence=_merge_limited(existing.evidence, classification.evidence, limit=20),
                stable_landmarks=_merge_landmarks(existing.stable_landmarks, stable_landmarks),
                dynamic_regions=classification.dynamic_regions or existing.dynamic_regions,
                negative_landmarks=_merge_landmarks(
                    existing.negative_landmarks, classification.negative_landmarks
                ),
                first_seen_at=existing.first_seen_at,
                last_seen_at=now,
                seen_count=existing.seen_count + 1,
                screenshot_hashes=_merge_limited(
                    existing.screenshot_hashes, [screenshot_hash], limit=self._max_hashes
                ),
            )

        pages[page_state] = record
        self._write(pages)
        return record

    def _materialize_templates(
        self,
        *,
        page_state: str,
        observation: Observation | None,
        landmarks: list[PageLandmark],
        existing_landmarks: list[PageLandmark],
    ) -> list[PageLandmark]:
        if not landmarks:
            return []

        reused = {lm.name: lm.template for lm in existing_landmarks if lm.template}
        image: Image.Image | None = None
        result: list[PageLandmark] = []

        for index, landmark in enumerate(landmarks):
            template = landmark.template or reused.get(landmark.name, "")
            if template:
                result.append(_with_template(landmark, template))
                continue

            if (
                landmark.type not in _TEMPLATE_LANDMARK_TYPES
                or landmark.region is None
                or observation is None
            ):
                result.append(landmark)
                continue

            if image is None:
                image = _decode(observation)
            bbox = _region_to_pixels(landmark.region, *image.size)
            if not _croppable(bbox, *image.size):
                result.append(landmark)
                continue

            rel = Path("templates") / page_state / f"{index:02d}_{landmark.name}.png"
            abs_path = self.template_root / rel
            os.makedirs(abs_path.parent, exist_ok=True)
            image.crop((bbox.x1, bbox.y1, bbox.x2, bbox.y2)).save(abs_path)
            result.append(_with_template(landmark, rel.as_posix()))

        return result

    def _read(self) -> dict[str, PageRecord]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                {"msg": "页面 registry 读取失败，按空库处理", "path": str(self._path), "error": str(e)}
            )
            return {}
        pages_raw = data.get("pages", data)
        return {
            normalize_page_state(str(raw.get("page_state") or key)): _record_from_raw(key, raw)
            for key, raw in pages_raw.items()
        }

    def _write(self, pages: dict[str, PageRecord]) -> None:
        os.makedirs(self._path.parent, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "pages": {
                name: _record_to_jsonable(record)
                for name, record in sorted(pages.items())
            },
        }
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False, indent=2))


def _record_summary(record: PageRecord) -> dict[str, Any]:
    return {
        "page_state": record.page_state,
        "description": record.description,
        "layout_type": record.layout_type,
        "seen_count": record.seen_count,
        "evidence": record.evidence[:3],
        "stable_landmarks": [_landmark_summary(lm) for lm in record.stable_landmarks[:8]],
        "negative_landmarks": [_landmark_summary(lm) for lm in record.negative_landmarks[:5]],
    }


def _landmark_summary(landmark: PageLandmark) -> dict[str, Any]:
    return {
        "name": landmark.name,
        "type": landmark.type,
        "description": landmark.description,
        "text": landmark.text,
        "required": landmark.required,
        "has_template": bool(landmark.template),
    }


def _record_from_raw(key: str, raw: dict) -> PageRecord:
    page_state = normalize_page_state(str(raw.get("page_state") or key))
    return PageRecord(
        page_state=page_state,
        description=str(raw.get("description") or ""),
        layout_type=str(raw.get("layout_type") or ""),
        evidence=[str(item) for item in raw.get("evidence") or []],
        stable_landmarks=_landmarks_from_raw(raw.get("stable_landmarks")),
        dynamic_regions=[item for item in raw.get("dynamic_regions") or [] if isinstance(item, dict)],
        negative_landmarks=_landmarks_from_raw(raw.get("negative_landmarks")),
        first_seen_at=str(raw.get("first_seen_at") or ""),
        last_seen_at=str(raw.get("last_seen_at") or ""),
        seen_count=int(raw.get("seen_count") or 0),
        screenshot_hashes=[str(item) for item in raw.get("screenshot_hashes") or []],
    )


def _record_to_jsonable(record: PageRecord) -> dict[str, Any]:
    return {
        "page_state": record.page_state,
        "description": record.description,
        "layout_type": record.layout_type,
        "evidence": record.evidence,
        "stable_landmarks": [lm.to_jsonable() for lm in record.stable_landmarks],
        "dynamic_regions": record.dynamic_regions,
        "negative_landmarks": [lm.to_jsonable() for lm in record.negative_landmarks],
        "first_seen_at": record.first_seen_at,
        "last_seen_at": record.last_seen_at,
        "seen_count": record.seen_count,
        "screenshot_hashes": record.screenshot_hashes,
    }


def _landmarks_from_raw(value: Any) -> list[PageLandmark]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        landmark = PageLandmark.from_raw(item)
        if landmark is not None:
            out.append(landmark)
    return out


def _merge_limited(existing: list[str], new_items: list[str], limit: int) -> list[str]:
    merged = [*existing]
    for item in new_items:
        if item and item not in merged:
            merged.append(item)
    return merged[-limit:]


def _merge_landmarks(
    existing: list[PageLandmark], new_items: list[PageLandmark]
) -> list[PageLandmark]:
    merged = {lm.name: lm for lm in existing}
    for landmark in new_items:
        current = merged.get(landmark.name)
        if current is None or (landmark.template and not current.template):
            merged[landmark.name] = landmark
    return list(merged.values())


def _with_template(landmark: PageLandmark, template: str) -> PageLandmark:
    return PageLandmark(
        name=landmark.name,
        type=landmark.type,
        description=landmark.description,
        region=landmark.region,
        text=landmark.text,
        template=template,
        required=landmark.required,
        weight=landmark.weight,
        threshold=landmark.threshold,
    )


def _decode(observation: Observation) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(observation.image_base64))).convert("RGB")


def _region_to_pixels(region: dict[str, float], width: int, height: int) -> BBox:
    x1, y1, x2, y2 = region["x1"], region["y1"], region["x2"], region["y2"]
    if max(x1, y1, x2, y2) <= 1.0:
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height
    return BBox(
        x1=max(0, min(width, int(x1))),
        y1=max(0, min(height, int(y1))),
        x2=max(0, min(width, int(x2))),
        y2=max(0, min(height, int(y2))),
    )


def _croppable(bbox: BBox, width: int, height: int) -> bool:
    cw = bbox.x2 - bbox.x1
    ch = bbox.y2 - bbox.y1
    if cw < 8 or ch < 8:
        return False
    area = (cw * ch) / max(1, width * height)
    return area <= 0.12 and cw / max(1, width) <= 0.6 and ch / max(1, height) <= 0.35


__all__ = ["PageRecord", "PageRegistry"]
