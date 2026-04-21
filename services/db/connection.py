"""SQLite 连接管理：单例 Db，WAL + 写锁。

用法：
    db = init_db("data/agent.db")
    with db.write() as conn:
        conn.execute("INSERT ...")
    # 读可以直接用 db.read()（WAL 允许并发读）
    rows = db.read().execute("SELECT ...").fetchall()
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class Db:
    """线程安全的 SQLite 连接包装。

    设计：
    - isolation_level=None 手动控制事务，避免 Python 自带 autocommit 干扰 WAL
    - 单连接 + 写锁（serialized writes），读者通过 WAL 无需锁
    - check_same_thread=False 允许从 to_thread 的工作线程访问
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._write_lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @contextmanager
    def write(self):
        """写事务：BEGIN IMMEDIATE 立即加写锁，不阻塞读者。"""
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self._conn
                self._conn.execute("COMMIT;")
            except BaseException:
                self._conn.execute("ROLLBACK;")
                raise

    def read(self) -> sqlite3.Connection:
        """返回可读的连接。WAL 模式下并发读不需要锁。"""
        return self._conn

    def vacuum(self) -> None:
        """回收 DB 空间 + 优化查询计划（调用时机：启动或 shutdown）。"""
        with self._write_lock:
            self._conn.execute("PRAGMA optimize;")
            # Note: VACUUM can't run inside a transaction, so we don't wrap it
            try:
                self._conn.execute("VACUUM;")
            except Exception:
                pass  # VACUUM may fail if WAL has uncommitted state

    def close(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.Error:
            pass
        self._conn.close()


_db: Db | None = None


def init_db(path: str | Path = "data/agent.db") -> Db:
    global _db
    if _db is None:
        _db = Db(path)
    return _db


def get_db() -> Db:
    if _db is None:
        raise RuntimeError("Db 未初始化，请先调 init_db()")
    return _db
