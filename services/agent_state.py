"""
Agent 状态管理：通用的状态读写服务。
负责读写 agent_state.json，与具体平台无关。

设计原则：
- get_state() / AgentStateRepo.get() 永远返回完整 schema 的 dict
- DEFAULT_STATE 是 state 字段默认值的唯一定义点
- 通过 AgentStateRepo 注入到 RunContext，可被替换为 InMemoryStateRepo 用于测试
"""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path
from typing import Protocol

import config
import utils.logger as logger

# Agent state 的完整 schema 与默认值
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


def _merge_and_trim(existing: list, new_items: list, limit: int) -> list:
    """有序去重追加，超出上限时从头部淘汰最旧的条目。"""
    merged = existing + [x for x in new_items if x not in existing]
    return merged[-limit:]


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
    """
    将 patch 字段合并进 state，返回新 state（不修改原对象）。
    所有"None 表示不更新该字段"的语义都集中在这里。
    """
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

    if inspiration_pool is not None:
        state["inspiration_pool"] = _merge_and_trim(
            state["inspiration_pool"], inspiration_pool, limits["inspiration_pool"]
        )
    if last_discovery is not None:
        state["last_discovery"] = last_discovery
    if title_few_shots is not None:
        state["title_few_shots"] = _merge_and_trim(state["title_few_shots"], title_few_shots, limits["title_few_shots"])
    if hashtags is not None:
        state["hashtags"] = _merge_and_trim(state["hashtags"], hashtags, limits["hashtags"])
    if anxiety_keywords is not None:
        state["anxiety_keywords"] = _merge_and_trim(
            state["anxiety_keywords"], anxiety_keywords, limits["anxiety_keywords"]
        )
    if knowledge_topics is not None:
        state["knowledge_topics"] = _merge_and_trim(
            state["knowledge_topics"], knowledge_topics, limits["knowledge_topics"]
        )
    if mood is not None:
        state["mood"] = mood
    if learning_notes is not None:
        state["learning_notes"] = _merge_and_trim(state["learning_notes"], learning_notes, limits["learning_notes"])
    if recent_searches is not None:
        state["recent_searches"] = _merge_and_trim(state["recent_searches"], recent_searches, limits["recent_searches"])

    if state["daily_stats"]["date"] != today_str:
        state["daily_stats"] = {
            "date": today_str,
            "posts_count": 0,
            "replies_count": 0,
        }
    if is_posted:
        state["daily_stats"]["posts_count"] += 1
        state["last_post_date"] = today_str
        state["last_post_trace_id"] = trace_id

    state["last_check_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    return state


# ═══════════════════════════════════════════════════════
# 仓储接口与实现
# ═══════════════════════════════════════════════════════


class AgentStateRepo(Protocol):
    """Agent 状态读写接口，由 RunContext 注入"""

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
    """文件后端：读写 JSON 到磁盘"""

    def __init__(self, file_path: str | Path, limits: dict):
        self._path = Path(file_path)
        self._limits = limits

    def get(self) -> dict:
        state = copy.deepcopy(DEFAULT_STATE)
        if self._path.exists():
            state.update(json.loads(self._path.read_text(encoding="utf-8")))
        return state

    def update(self, **kwargs) -> dict:
        try:
            state = _apply_patch(self.get(), self._limits, **kwargs)
            from utils.atomic_write import atomic_write_text
            atomic_write_text(self._path, json.dumps(state, ensure_ascii=False, indent=4))
            logger.info({"msg": "状态同步成功"}, kwargs.get("trace_id", "system"))
            return state
        except Exception as e:
            logger.error({"msg": "同步状态失败", "error": str(e)}, kwargs.get("trace_id", "system"))
            raise


class InMemoryStateRepo:
    """内存后端，用于测试。直接持有一个 dict，不写盘。"""

    def __init__(self, limits: dict, initial: dict | None = None):
        self._limits = limits
        self._state = copy.deepcopy(DEFAULT_STATE)
        if initial:
            self._state.update(initial)

    def get(self) -> dict:
        return copy.deepcopy(self._state)

    def update(self, **kwargs) -> dict:
        """复用 _apply_patch 的合并逻辑，但跳过磁盘 I/O"""
        self._state = _apply_patch(self._state, self._limits, **kwargs)
        return copy.deepcopy(self._state)


# ═══════════════════════════════════════════════════════
# 模块级单例 + 兼容函数
# ═══════════════════════════════════════════════════════

_default_repo: AgentStateRepo | None = None


def default_repo() -> AgentStateRepo:
    """模块级默认 repo（基于 config 中的路径），供 skills 兼容代理使用"""
    global _default_repo
    if _default_repo is None:
        _default_repo = JsonFileStateRepo(
            file_path=config.agent["storage"]["state_file"],
            limits=config.agent["state_limits"],
        )
    return _default_repo


def get_state() -> dict:
    """兼容旧调用：从默认 repo 读取状态"""
    return default_repo().get()


def sync_state(**kwargs) -> dict:
    """兼容旧调用：通过默认 repo 更新状态"""
    return default_repo().update(**kwargs)
