"""TokenUsageRepo: LLM 调用消耗日志的 SQLite 存储。"""

from __future__ import annotations

from datetime import datetime, timedelta

import utils.logger as logger
from services.db.connection import Db


class TokenUsageRepo:
    def __init__(self, db: Db):
        self._db = db

    def log(
        self,
        *,
        trace_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        account_id: str = "default",
    ) -> None:
        """写入一条 token 消耗记录。DB 写入失败仅记日志，不抛异常阻塞 agent 主流程。"""
        now = datetime.now()
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO token_usage
                        (account_id, date, timestamp, logged_at, trace_id, model,
                         prompt_tokens, completion_tokens, total_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        now.strftime("%Y-%m-%d"),
                        now.strftime("%H:%M:%S"),
                        now.isoformat(),
                        trace_id,
                        model,
                        prompt_tokens,
                        completion_tokens,
                        prompt_tokens + completion_tokens,
                    ),
                )
        except Exception as e:
            logger.warning(
                {"msg": "token_usage DB 写入失败", "model": model, "error": str(e)},
                trace_id,
            )
            raise

    def daily_stats(self, date: str) -> dict:
        """某日统计：total/prompt/completion tokens + call_count + models breakdown。"""
        rows = self._db.read().execute(
            """
            SELECT model, prompt_tokens, completion_tokens, total_tokens
            FROM token_usage WHERE date = ?
            """,
            (date,),
        ).fetchall()
        stats: dict = {
            "date": date,
            "total_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "call_count": len(rows),
            "models": {},
        }
        for r in rows:
            stats["total_tokens"] += r["total_tokens"]
            stats["total_prompt_tokens"] += r["prompt_tokens"]
            stats["total_completion_tokens"] += r["completion_tokens"]
            m = r["model"] or "unknown"
            stats["models"][m] = stats["models"].get(m, 0) + r["total_tokens"]
        return stats

    def details(self, date: str, page: int, page_size: int) -> tuple[list[dict], int]:
        total = self._db.read().execute(
            "SELECT COUNT(*) AS c FROM token_usage WHERE date = ?", (date,)
        ).fetchone()["c"]
        if total == 0:
            return [], 0
        offset = (page - 1) * page_size
        rows = self._db.read().execute(
            """
            SELECT timestamp, trace_id, model, prompt_tokens, completion_tokens, total_tokens
            FROM token_usage WHERE date = ? ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (date, page_size, offset),
        ).fetchall()
        data = [
            {
                "timestamp": r["timestamp"],
                "trace_id": r["trace_id"],
                "model": r["model"],
                "prompt": r["prompt_tokens"],
                "completion": r["completion_tokens"],
                "total": r["total_tokens"],
            }
            for r in rows
        ]
        return data, total

    def list_dates(self) -> list[str]:
        rows = self._db.read().execute(
            "SELECT DISTINCT date FROM token_usage ORDER BY date DESC"
        ).fetchall()
        return [r["date"] for r in rows]

    def cleanup_older_than(self, days: int) -> int:
        """删除 logged_at 超过 N 天的 token 消耗记录。返回删除行数。"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._db.write() as conn:
            cur = conn.execute("DELETE FROM token_usage WHERE logged_at < ?", (cutoff,))
            deleted = cur.rowcount
        if deleted > 0:
            logger.info({"msg": "token_usage 清理旧数据", "deleted": deleted, "days": days})
        return deleted
