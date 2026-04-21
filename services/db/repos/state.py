"""StateRepo: agent_state 的 SQLite 实现（实现 AgentStateRepo Protocol）。

与 JsonFileStateRepo 对齐 AgentStateRepo Protocol，消费方无感切换。
列表字段以 JSON 字符串存储（每个列表都被 state_limits 限制在百条以内）。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime

import utils.logger as logger
from services.agent_state import DEFAULT_STATE, _apply_patch
from services.db.connection import Db

# JSON 数组字段名 → 列名
_LIST_COLUMNS = {
    "inspiration_pool": "inspiration_pool_json",
    "title_few_shots": "title_few_shots_json",
    "hashtags": "hashtags_json",
    "anxiety_keywords": "anxiety_keywords_json",
    "knowledge_topics": "knowledge_topics_json",
    "learning_notes": "learning_notes_json",
    "recent_searches": "recent_searches_json",
    "followers_history": "followers_history_json",
}


class StateRepo:
    """SQLite 后端：一个 account_id 一行，列表字段存 JSON。实现 AgentStateRepo Protocol。"""

    def __init__(self, db: Db, limits: dict, account_id: str = "default"):
        self._db = db
        self._limits = limits
        self._account_id = account_id

    def get(self) -> dict:
        row = self._db.read().execute(
            "SELECT * FROM agent_state WHERE account_id = ?", (self._account_id,)
        ).fetchone()
        state = copy.deepcopy(DEFAULT_STATE)
        if not row:
            return state
        state["mood"] = row["mood"]
        state["last_discovery"] = row["last_discovery"]
        state["last_check_time"] = row["last_check_time"]
        state["last_post_date"] = row["last_post_date"]
        state["last_post_trace_id"] = row["last_post_trace_id"]
        try:
            state["daily_stats"] = json.loads(row["daily_stats_json"])
        except Exception:
            pass
        for field, col in _LIST_COLUMNS.items():
            try:
                state[field] = json.loads(row[col])
            except Exception:
                pass
        return state

    def update(self, **kwargs) -> dict:
        try:
            state = _apply_patch(self.get(), self._limits, **kwargs)
            now = datetime.now().isoformat()
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_state (
                        account_id, mood, last_discovery, last_check_time,
                        last_post_date, last_post_trace_id, daily_stats_json,
                        inspiration_pool_json, title_few_shots_json, hashtags_json,
                        anxiety_keywords_json, knowledge_topics_json, learning_notes_json,
                        recent_searches_json, followers_history_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        mood=excluded.mood,
                        last_discovery=excluded.last_discovery,
                        last_check_time=excluded.last_check_time,
                        last_post_date=excluded.last_post_date,
                        last_post_trace_id=excluded.last_post_trace_id,
                        daily_stats_json=excluded.daily_stats_json,
                        inspiration_pool_json=excluded.inspiration_pool_json,
                        title_few_shots_json=excluded.title_few_shots_json,
                        hashtags_json=excluded.hashtags_json,
                        anxiety_keywords_json=excluded.anxiety_keywords_json,
                        knowledge_topics_json=excluded.knowledge_topics_json,
                        learning_notes_json=excluded.learning_notes_json,
                        recent_searches_json=excluded.recent_searches_json,
                        followers_history_json=excluded.followers_history_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        self._account_id,
                        state["mood"],
                        state["last_discovery"],
                        state["last_check_time"],
                        state["last_post_date"],
                        state["last_post_trace_id"],
                        json.dumps(state["daily_stats"], ensure_ascii=False),
                        *[
                            json.dumps(state[field], ensure_ascii=False)
                            for field in _LIST_COLUMNS
                        ],
                        now,
                    ),
                )
            logger.info({"msg": "状态同步成功", "account": self._account_id}, kwargs.get("trace_id", "system"))
            return state
        except Exception as e:
            logger.error({"msg": "同步状态失败", "error": str(e)}, kwargs.get("trace_id", "system"))
            raise
