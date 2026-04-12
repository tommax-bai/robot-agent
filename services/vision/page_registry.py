"""持久化页面注册表：存储 LLM 识别的页面状态及其视觉 landmark。"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

import utils.logger as logger
from services.vision.locator import BBox
from services.vision.page_classifier import PageClassification, PageLandmark, normalize_page_state

if TYPE_CHECKING:
    from agents.base import Observation


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
    """Small JSON registry of known semantic page states."""

    def __init__(self, path: str | Path = "data/page_registry/pages.json", max_hashes_per_page: int = 20):
        self._path = Path(path)
        self._max_hashes_per_page = max_hashes_per_page

    @property
    def template_root(self) -> Path:
        return self._path.parent

    def list_records(self) -> list[PageRecord]:
        return list(self._read().values())

    def has_page_state(self, page_state: str) -> bool:
        return normalize_page_state(page_state) in self._read()

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
        now = datetime.now().isoformat()
        existing = pages.get(page_state)
        stable_landmarks = self._materialize_templates(
            page_state=page_state,
            observation=observation,
            landmarks=classification.stable_landmarks,
            existing_landmarks=existing.stable_landmarks if existing else [],
        )

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
                negative_landmarks=_merge_landmarks(existing.negative_landmarks, classification.negative_landmarks),
                first_seen_at=existing.first_seen_at,
                last_seen_at=now,
                seen_count=existing.seen_count + 1,
                screenshot_hashes=_merge_limited(
                    existing.screenshot_hashes, [screenshot_hash], limit=self._max_hashes_per_page
                ),
            )

        pages[page_state] = record
        self._write(pages)
        return record

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "page_state": record.page_state,
                "description": record.description,
                "layout_type": record.layout_type,
                "seen_count": record.seen_count,
                "evidence": record.evidence[:3],
                "stable_landmarks": [_landmark_summary(landmark) for landmark in record.stable_landmarks[:8]],
                "negative_landmarks": [_landmark_summary(landmark) for landmark in record.negative_landmarks[:5]],
            }
            for record in self.list_records()
        ]

    def _read(self) -> dict[str, PageRecord]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning({"msg": "页面 registry 读取失败，按空库处理", "path": str(self._path), "error": str(e)})
            return {}
        pages = data.get("pages", data)
        records: dict[str, PageRecord] = {}
        for key, value in pages.items():
            page_state = normalize_page_state(str(value.get("page_state") or key))
            records[page_state] = PageRecord(
                page_state=page_state,
                description=str(value.get("description") or ""),
                layout_type=str(value.get("layout_type") or ""),
                evidence=[str(item) for item in value.get("evidence") or []],
                stable_landmarks=_read_landmarks(value.get("stable_landmarks")),
                dynamic_regions=_read_regions(value.get("dynamic_regions")),
                negative_landmarks=_read_landmarks(value.get("negative_landmarks")),
                first_seen_at=str(value.get("first_seen_at") or ""),
                last_seen_at=str(value.get("last_seen_at") or ""),
                seen_count=int(value.get("seen_count") or 0),
                screenshot_hashes=[str(item) for item in value.get("screenshot_hashes") or []],
            )
        return records

    def _materialize_templates(
        self,
        *,
        page_state: str,
        observation: Observation | None,
        landmarks: list[PageLandmark],
        existing_landmarks: list[PageLandmark],
    ) -> list[PageLandmark]:
        existing_templates = {landmark.name: landmark.template for landmark in existing_landmarks if landmark.template}
        if not landmarks:
            return []

        image: Image.Image | None = None
        materialized: list[PageLandmark] = []
        for index, landmark in enumerate(landmarks):
            template = landmark.template or existing_templates.get(landmark.name, "")
            if template:
                materialized.append(_replace_landmark_template(landmark, template))
                continue

            if landmark.type not in _TEMPLATE_LANDMARK_TYPES or landmark.region is None or observation is None:
                materialized.append(landmark)
                continue

            if image is None:
                image = _decode_observation(observation)
            bbox = _region_to_pixels(landmark.region, *image.size)
            if not _is_template_crop_candidate(bbox, *image.size):
                materialized.append(landmark)
                continue

            rel_path = Path("templates") / page_state / f"{index:02d}_{landmark.name}.png"
            abs_path = self.template_root / rel_path
            os.makedirs(abs_path.parent, exist_ok=True)
            image.crop((bbox.x1, bbox.y1, bbox.x2, bbox.y2)).save(abs_path)
            materialized.append(_replace_landmark_template(landmark, rel_path.as_posix()))

        return materialized

    def _write(self, pages: dict[str, PageRecord]) -> None:
        os.makedirs(self._path.parent, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "pages": {
                page_state: {
                    "page_state": record.page_state,
                    "description": record.description,
                    "layout_type": record.layout_type,
                    "evidence": record.evidence,
                    "stable_landmarks": [landmark.to_jsonable() for landmark in record.stable_landmarks],
                    "dynamic_regions": record.dynamic_regions,
                    "negative_landmarks": [landmark.to_jsonable() for landmark in record.negative_landmarks],
                    "first_seen_at": record.first_seen_at,
                    "last_seen_at": record.last_seen_at,
                    "seen_count": record.seen_count,
                    "screenshot_hashes": record.screenshot_hashes,
                }
                for page_state, record in sorted(pages.items())
            },
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_limited(existing: list[str], new_items: list[str], limit: int) -> list[str]:
    merged = [*existing]
    for item in new_items:
        if item and item not in merged:
            merged.append(item)
    return merged[-limit:]


def _merge_landmarks(existing: list[PageLandmark], new_items: list[PageLandmark]) -> list[PageLandmark]:
    merged = {landmark.name: landmark for landmark in existing}
    for landmark in new_items:
        current = merged.get(landmark.name)
        if current is None:
            merged[landmark.name] = landmark
            continue
        merged[landmark.name] = landmark if landmark.template or not current.template else current
    return list(merged.values())


def _read_landmarks(value: Any) -> list[PageLandmark]:
    if not isinstance(value, list):
        return []
    landmarks: list[PageLandmark] = []
    for item in value:
        landmark = PageLandmark.from_raw(item)
        if landmark is not None:
            landmarks.append(landmark)
    return landmarks


def _read_regions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _landmark_summary(landmark: PageLandmark) -> dict[str, Any]:
    return {
        "name": landmark.name,
        "type": landmark.type,
        "description": landmark.description,
        "text": landmark.text,
        "required": landmark.required,
        "has_template": bool(landmark.template),
    }


def _replace_landmark_template(landmark: PageLandmark, template: str) -> PageLandmark:
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


def _decode_observation(observation: Observation) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(observation.image_base64))).convert("RGB")


def _region_to_pixels(region: dict[str, float], width: int, height: int) -> BBox:
    x1 = region["x1"]
    y1 = region["y1"]
    x2 = region["x2"]
    y2 = region["y2"]
    if max(x1, y1, x2, y2) <= 1.0:
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height
    return BBox(
        x1=max(0, min(width, int(x1))),
        y1=max(0, min(height, int(y1))),
        x2=max(0, min(width, int(x2))),
        y2=max(0, min(height, int(y2))),
    )


def _is_template_crop_candidate(bbox: BBox, width: int, height: int) -> bool:
    crop_width = bbox.x2 - bbox.x1
    crop_height = bbox.y2 - bbox.y1
    if crop_width < 8 or crop_height < 8:
        return False
    area_ratio = (crop_width * crop_height) / max(1, width * height)
    width_ratio = crop_width / max(1, width)
    height_ratio = crop_height / max(1, height)
    return area_ratio <= 0.12 and width_ratio <= 0.6 and height_ratio <= 0.35
