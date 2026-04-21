"""TaskHistoryRepo: 任务历史的 SQLite 存储。"""

from __future__ import annotations

from datetime import datetime, timedelta

import utils.logger as logger
from services.db.connection import Db


class TaskHistoryRepo:
    def __init__(self, db: Db):
        self._db = db

    def init_task(
        self,
        trace_id: str,
        goal: str,
        account_id: str = "default",
    ) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tasks
                        (trace_id, account_id, created_at, goal, status)
                    VALUES (?, ?, ?, ?, 'running')
                    """,
                    (trace_id, account_id, now, goal),
                )
        except Exception as e:
            logger.error({"msg": "tasks init 失败", "error": str(e)}, trace_id)
            raise

    def append_markdown(self, trace_id: str, chunk: str) -> None:
        try:
            with self._db.write() as conn:
                conn.execute(
                    "UPDATE tasks SET markdown_body = COALESCE(markdown_body, '') || ? WHERE trace_id = ?",
                    (chunk, trace_id),
                )
        except Exception as e:
            logger.warning({"msg": "tasks markdown 追加失败", "error": str(e)}, trace_id)

    def set_status(self, trace_id: str, status: str) -> None:
        finished = datetime.now().isoformat() if status in ("completed", "failed") else None
        try:
            with self._db.write() as conn:
                conn.execute(
                    "UPDATE tasks SET status = ?, finished_at = COALESCE(?, finished_at) WHERE trace_id = ?",
                    (status, finished, trace_id),
                )
        except Exception as e:
            logger.warning({"msg": "tasks status 更新失败", "status": status, "error": str(e)}, trace_id)

    def list_recent(self, limit: int = 50, account_id: str | None = None) -> list[dict]:
        if account_id:
            rows = self._db.read().execute(
                """
                SELECT trace_id, account_id, created_at, goal, status, finished_at
                FROM tasks WHERE account_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        else:
            rows = self._db.read().execute(
                """
                SELECT trace_id, account_id, created_at, goal, status, finished_at
                FROM tasks ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, trace_id: str) -> dict | None:
        row = self._db.read().execute(
            "SELECT * FROM tasks WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return dict(row) if row else None

    def cleanup_older_than(self, days: int) -> int:
        """删除 finished_at 超过 N 天且 status='completed'/'failed' 的任务。返回删除行数。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._db.write() as conn:
            cur = conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'failed')
                  AND finished_at IS NOT NULL
                  AND finished_at < ?
                """,
                (cutoff,),
            )
            deleted = cur.rowcount
        if deleted > 0:
            logger.info({"msg": "tasks 清理旧数据", "deleted": deleted, "days": days})
        return deleted
