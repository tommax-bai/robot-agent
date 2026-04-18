"""services.state: Agent 状态读写服务。

设计要点：
- `DEFAULT_STATE` 是唯一的 schema 真相源；`get()` 始终返回完整 schema 的副本。
- 通过 `AgentStateRepo` 协议注入到 `RunContext`，测试可替换为 `InMemoryStateRepo`。
- 不再维护模块级单例 / `get_state()` / `sync_state()` 旧 API —— 状态必须通过
  注入的 repo 访问，杜绝隐式全局状态。
- `update()` 的 12 个 kwargs 集中在 `_apply_patch` 里处理 "None 表示不更新" 语义，
  保持调用点（supervisor/jobs/knowledge）的可读性。
"""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path
from typing import Protocol

import utils.logger as logger
from utils.fs import atomic_write_text

DEFAULT_STATE: dict = {
    "inspiration_pool": [],
    "title_few_shots": [],
    "hashtags": [],
    "anxiety_keywords": [],
    "knowledge_topics": [],
    "learning_notes": [],
    "recent_searches": [],
    "followers_history": [],
    "mood": "neutral",
    "last_discovery": "",
    "last_check_time": "",
    "last_post_date": "",
    "last_post_trace_id": "",
    "daily_stats": {
        "date": "",
        "posts_count": 0,
        "replies_count": 0,
    },
}


class AgentStateRepo(Protocol):
    """状态读写接口。"""

    def get(self) -> dict: ...

    def update(
        self,
        *,
        followers: int | None = None,
        likes: int | None = None,
        is_posted: bool = False,
        inspiration_pool: list | None = None,
        last_discovery: str | None = None,
        title_few_shots: list | None = None,
        mood: str | None = None,
        learning_notes: list | None = None,
        hashtags: list | None = None,
        anxiety_keywords: list | None = None,
        knowledge_topics: list | None = None,
        recent_searches: list | None = None,
        trace_id: str = "system",
    ) -> dict: ...


class JsonFileStateRepo:
    """文件后端：读写 JSON 到磁盘（atomic write）。"""

    def __init__(self, file_path: str | Path, limits: dict):
        self._path = Path(file_path)
        self._limits = limits

    def get(self) -> dict:
        state = copy.deepcopy(DEFAULT_STATE)
        if self._path.exists():
            state.update(json.loads(self._path.read_text(encoding="utf-8")))
        return state

    def update(self, **kwargs) -> dict:
        trace_id = kwargs.get("trace_id", "system")
        try:
            state = _apply_patch(self.get(), self._limits, **kwargs)
            atomic_write_text(self._path, json.dumps(state, ensure_ascii=False, indent=4))
            logger.info({"msg": "状态同步成功"}, trace_id)
            return state
        except Exception as e:
            logger.error({"msg": "同步状态失败", "error": str(e)}, trace_id)
            raise


class InMemoryStateRepo:
    """内存后端，用于测试。"""

    def __init__(self, limits: dict, initial: dict | None = None):
        self._limits = limits
        self._state = copy.deepcopy(DEFAULT_STATE)
        if initial:
            self._state.update(initial)

    def get(self) -> dict:
        return copy.deepcopy(self._state)

    def update(self, **kwargs) -> dict:
        self._state = _apply_patch(self._state, self._limits, **kwargs)
        return copy.deepcopy(self._state)


def _apply_patch(
    state: dict,
    limits: dict,
    *,
    followers: int | None = None,
    likes: int | None = None,
    is_posted: bool = False,
    inspiration_pool: list | None = None,
    last_discovery: str | None = None,
    title_few_shots: list | None = None,
    mood: str | None = None,
    learning_notes: list | None = None,
    hashtags: list | None = None,
    anxiety_keywords: list | None = None,
    knowledge_topics: list | None = None,
    recent_searches: list | None = None,
    trace_id: str = "system",
) -> dict:
    state = copy.deepcopy(state)
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if followers is not None:
        state["followers_history"].append(
            {
                "date": today_str,
                "time": now.strftime("%H:%M:%S"),
                "followers": followers,
                "likes": likes,
            }
        )
        state["followers_history"] = state["followers_history"][-limits["followers_history"] :]

    _merge_field(state, "inspiration_pool", inspiration_pool, limits)
    _merge_field(state, "title_few_shots", title_few_shots, limits)
    _merge_field(state, "hashtags", hashtags, limits)
    _merge_field(state, "anxiety_keywords", anxiety_keywords, limits)
    _merge_field(state, "knowledge_topics", knowledge_topics, limits)
    _merge_field(state, "learning_notes", learning_notes, limits)
    _merge_field(state, "recent_searches", recent_searches, limits)

    if last_discovery is not None:
        state["last_discovery"] = last_discovery
    if mood is not None:
        state["mood"] = mood

    if state["daily_stats"]["date"] != today_str:
        state["daily_stats"] = {"date": today_str, "posts_count": 0, "replies_count": 0}
    if is_posted:
        state["daily_stats"]["posts_count"] += 1
        state["last_post_date"] = today_str
        state["last_post_trace_id"] = trace_id

    state["last_check_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return state


def _merge_field(state: dict, key: str, new_items: list | None, limits: dict) -> None:
    if new_items is None:
        return
    existing = state[key]
    merged = existing + [x for x in new_items if x not in existing]
    state[key] = merged[-limits[key] :]


__all__ = ["AgentStateRepo", "DEFAULT_STATE", "InMemoryStateRepo", "JsonFileStateRepo"]
