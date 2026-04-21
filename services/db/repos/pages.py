"""PageRepo: pages + page_screenshot_hashes 的 SQLite 存储。"""

from __future__ import annotations

import json
from datetime import datetime

import utils.logger as logger
from services.db.connection import Db


class PageRepo:
    def __init__(self, db: Db, max_hashes_per_page: int = 20):
        self._db = db
        self._max_hashes = max_hashes_per_page

    def get_all_raw(self) -> dict[str, dict]:
        """返回 {page_state: record_dict} 供 PageRegistry 包装成 PageRecord。

        单次查询拉所有 hash，按 page_state 分组，避免 N+1。
        """
        page_rows = self._db.read().execute("SELECT * FROM pages").fetchall()
        hash_rows = self._db.read().execute(
            "SELECT page_state, hash FROM page_screenshot_hashes ORDER BY seen_at"
        ).fetchall()
        hashes_by_page: dict[str, list[str]] = {}
        for hr in hash_rows:
            hashes_by_page.setdefault(hr["page_state"], []).append(hr["hash"])
        out: dict[str, dict] = {}
        for r in page_rows:
            out[r["page_state"]] = {
                "page_state": r["page_state"],
                "description": r["description"],
                "layout_type": r["layout_type"],
                "evidence": json.loads(r["evidence_json"]),
                "stable_landmarks": json.loads(r["stable_landmarks_json"]),
                "dynamic_regions": json.loads(r["dynamic_regions_json"]),
                "negative_landmarks": json.loads(r["negative_landmarks_json"]),
                "first_seen_at": r["first_seen_at"],
                "last_seen_at": r["last_seen_at"],
                "seen_count": r["seen_count"],
                "screenshot_hashes": hashes_by_page.get(r["page_state"], []),
            }
        return out

    def upsert_record(self, record_dict: dict) -> None:
        """写入 pages 表 + 同步 screenshot_hashes 子表（同一事务）。"""
        now = datetime.now().isoformat()
        page_state = record_dict["page_state"]
        try:
            with self._db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO pages (
                        page_state, description, layout_type, evidence_json,
                        stable_landmarks_json, dynamic_regions_json, negative_landmarks_json,
                        first_seen_at, last_seen_at, seen_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(page_state) DO UPDATE SET
                        description=excluded.description,
                        layout_type=excluded.layout_type,
                        evidence_json=excluded.evidence_json,
                        stable_landmarks_json=excluded.stable_landmarks_json,
                        dynamic_regions_json=excluded.dynamic_regions_json,
                        negative_landmarks_json=excluded.negative_landmarks_json,
                        last_seen_at=excluded.last_seen_at,
                        seen_count=excluded.seen_count
                    """,
                    (
                        page_state,
                        record_dict.get("description", ""),
                        record_dict.get("layout_type", ""),
                        json.dumps(record_dict.get("evidence", []), ensure_ascii=False),
                        json.dumps(record_dict.get("stable_landmarks", []), ensure_ascii=False),
                        json.dumps(record_dict.get("dynamic_regions", []), ensure_ascii=False),
                        json.dumps(record_dict.get("negative_landmarks", []), ensure_ascii=False),
                        record_dict.get("first_seen_at", now),
                        record_dict.get("last_seen_at", now),
                        int(record_dict.get("seen_count", 0)),
                    ),
                )
                # hashes: 全量替换（保留最新 N 条），与 pages 更新在同一事务
                conn.execute("DELETE FROM page_screenshot_hashes WHERE page_state = ?", (page_state,))
                hashes = record_dict.get("screenshot_hashes", [])[-self._max_hashes:]
                for h in hashes:
                    conn.execute(
                        "INSERT OR IGNORE INTO page_screenshot_hashes (page_state, hash, seen_at) VALUES (?, ?, ?)",
                        (page_state, h, now),
                    )
            logger.info(
                {"msg": "page 已写入 DB", "page_state": page_state, "seen_count": record_dict.get("seen_count", 0)}
            )
        except Exception as e:
            logger.error({"msg": "page upsert 失败", "page_state": page_state, "error": str(e)})
            raise
