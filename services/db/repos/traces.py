"""TraceRepo: action_traces 的 SQLite 存储。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta

import utils.logger as logger
from services.db.connection import Db


class TraceRepo:
    def __init__(self, db: Db):
        self._db = db

    def append(self, trace_id: str, payload: dict) -> None:
        """追加一条事件。payload 含 event / time / subtask / step / page_state 等。"""
        subtask = payload.get("subtask") or {}
        subtask_id = subtask.get("id") if isinstance(subtask, dict) else None
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO action_traces
                        (trace_id, subtask_id, step, event, time, page_state, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        subtask_id,
                        payload.get("step"),
                        payload.get("event", ""),
                        payload.get("time", ""),
                        payload.get("page_state"),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        except Exception as e:
            logger.error({"msg": "action_traces 写入失败", "event": payload.get("event"), "error": str(e)}, trace_id)
            raise

    def read_events(
        self,
        trace_id: str,
        *,
        event: str | None = None,
        subtask_id: str | None = None,
    ) -> Iterator[dict]:
        query = "SELECT payload_json FROM action_traces WHERE trace_id = ?"
        params: list = [trace_id]
        if event is not None:
            query += " AND event = ?"
            params.append(event)
        if subtask_id is not None:
            query += " AND subtask_id = ?"
            params.append(subtask_id)
        query += " ORDER BY id"
        for row in self._db.read().execute(query, params):
            try:
                yield json.loads(row["payload_json"])
            except Exception:
                continue

    def cleanup_older_than(self, days: int) -> int:
        """删除超过 N 天的 trace 事件。返回删除行数。

        time 列是 ISO 字符串；与 (now - days).isoformat() 字典序比较，
        因为 ISO-8601 字符串的字典序与时间序一致。
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._db.write() as conn:
            cur = conn.execute("DELETE FROM action_traces WHERE time < ?", (cutoff,))
            deleted = cur.rowcount
        if deleted > 0:
            logger.info({"msg": "action_traces 清理旧数据", "deleted": deleted, "days": days})
        return deleted
