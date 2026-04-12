"""
页面上下文缓存：按 trace_id 缓存页面分类结果，支持后台异步分类。
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

import utils.logger as logger
from agents.base import Observation
from services.vision.page_classifier import PageClassifier, UnknownPageClassifier


@dataclass(frozen=True)
class PageContext:
    observation: Observation
    page_state: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    is_new_page: bool = False
    description: str = ""
    screenshot_hash: str = ""


class PageContextCache:
    """Cache the latest page context per trace_id."""

    def __init__(
        self,
        classifier: PageClassifier | None = None,
        *,
        background: bool = False,
        max_workers: int = 1,
    ):
        self._latest: dict[str, PageContext] = {}
        self._inflight: dict[str, Future[PageContext]] = {}
        self._lock = threading.Lock()
        self._classifier = classifier or UnknownPageClassifier()
        self._background = background
        self._executor = (
            ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="page-classifier")
            if background
            else None
        )

    def get_or_classify(self, trace_id: str, observation: Observation) -> PageContext:
        screenshot_hash = self._hash_observation(observation)
        with self._lock:
            cached = self._latest.get(trace_id)
            if cached and cached.screenshot_hash == screenshot_hash:
                return cached

        if self._background:
            return self._get_or_schedule(trace_id, observation, screenshot_hash)

        context = self._classify_context(trace_id, observation, screenshot_hash)
        with self._lock:
            self._latest[trace_id] = context
        return context

    def invalidate(self, trace_id: str) -> None:
        with self._lock:
            self._latest.pop(trace_id, None)
            for key, future in list(self._inflight.items()):
                if key.startswith(f"{trace_id}:"):
                    future.cancel()
                    self._inflight.pop(key, None)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _get_or_schedule(self, trace_id: str, observation: Observation, screenshot_hash: str) -> PageContext:
        key = f"{trace_id}:{screenshot_hash}"
        with self._lock:
            future = self._inflight.get(key)
            if future is not None and future.done():
                self._inflight.pop(key, None)
                try:
                    context = future.result()
                except Exception as e:
                    logger.warning({"msg": "后台页面分类失败，返回 unknown", "error": str(e)}, trace_id)
                    return self._unknown_context(observation, screenshot_hash, "page_classification_failed")
                self._latest[trace_id] = context
                logger.info(
                    {
                        "msg": "后台页面分类结果已应用",
                        "page_state": context.page_state,
                        "confidence": context.confidence,
                    },
                    trace_id,
                )
                return context

            if future is None:
                if self._executor is None:
                    return self._unknown_context(observation, screenshot_hash, "page_classification_executor_missing")
                self._cancel_stale_inflight_locked(trace_id, keep_key=key)
                self._inflight[key] = self._executor.submit(
                    self._classify_context, trace_id, observation, screenshot_hash
                )
                logger.info({"msg": "后台页面分类已调度"}, trace_id)

        return self._unknown_context(observation, screenshot_hash, "page_classification_pending")

    def _cancel_stale_inflight_locked(self, trace_id: str, keep_key: str) -> None:
        for key, future in list(self._inflight.items()):
            if key == keep_key or not key.startswith(f"{trace_id}:"):
                continue
            future.cancel()
            self._inflight.pop(key, None)

    def _classify_context(self, trace_id: str, observation: Observation, screenshot_hash: str) -> PageContext:
        classification = self._classifier.classify(observation, trace_id=trace_id)
        context = PageContext(
            observation=observation,
            page_state=classification.page_state,
            confidence=classification.confidence,
            evidence=classification.evidence,
            is_new_page=classification.is_new_page,
            description=classification.description,
            screenshot_hash=screenshot_hash,
        )
        return context

    @staticmethod
    def _unknown_context(observation: Observation, screenshot_hash: str, reason: str) -> PageContext:
        return PageContext(
            observation=observation,
            page_state="unknown",
            confidence=0.0,
            evidence=[reason],
            screenshot_hash=screenshot_hash,
        )

    @staticmethod
    def _hash_observation(observation: Observation) -> str:
        return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]
