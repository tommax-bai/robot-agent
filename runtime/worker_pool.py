"""
WorkerPool：多账号 worker 管理。

启动期从 config.agent["accounts"] 批量实例化（空列表时兜底一个 "default" worker）。
运行期可通过 AppContainer.add_worker / remove_worker 动态增删，dashboard 的
POST /agent/workers、DELETE /agent/workers/{id} 走这套。

API：
    pool.add(worker)            注册
    pool.remove(account_id)     移除（返回被摘下的 worker；任务活跃 / 最后一个时 refuse）
    pool.get(account_id)        按 id 取
    pool.default()              取默认（第一个注册的，或显式 mark）
    pool.list()                 全部 worker
    pool.has(account_id)        判断
    pool.shutdown_all()         应用关闭时统一释放云端 session
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.worker import Worker


class WorkerPool:
    def __init__(self):
        self._workers: dict[str, Worker] = {}
        self._default_id: str | None = None

    def add(self, worker: Worker, default: bool = False) -> None:
        if worker.account_id in self._workers:
            raise ValueError(f"worker account_id={worker.account_id} 已存在")
        self._workers[worker.account_id] = worker
        if default or self._default_id is None:
            self._default_id = worker.account_id

    def remove(self, account_id: str) -> Worker:
        """从池里摘下并返回 worker。调用方负责后续 shutdown。

        - 不存在 → KeyError
        - 池中只剩这一个 → RuntimeError（保证 default 永远有得指）
        - 任务活跃 → RuntimeError（避免抽走正在用的 supervisor）
        """
        if account_id not in self._workers:
            raise KeyError(f"未注册的 worker account_id={account_id}")
        if len(self._workers) <= 1:
            raise RuntimeError("WorkerPool 不能移除最后一个 worker（系统至少需要一个 default）")
        worker = self._workers[account_id]
        if worker.supervisor.has_active_task():
            run = worker.supervisor.current_run
            raise RuntimeError(
                f"worker={account_id} 当前任务 trace={run.trace_id} 正在运行，"
                "请先取消任务再移除"
            )
        del self._workers[account_id]
        if self._default_id == account_id:
            self._default_id = next(iter(self._workers), None)
        return worker

    def get(self, account_id: str) -> Worker:
        if account_id not in self._workers:
            raise KeyError(f"未注册的 worker account_id={account_id}")
        return self._workers[account_id]

    def default(self) -> Worker:
        if self._default_id is None:
            raise RuntimeError("WorkerPool 中无任何 worker")
        return self._workers[self._default_id]

    def has(self, account_id: str) -> bool:
        return account_id in self._workers

    def list(self) -> list[Worker]:
        return list(self._workers.values())

    @property
    def default_id(self) -> str | None:
        return self._default_id

    def shutdown_all(self) -> None:
        for w in self._workers.values():
            try:
                w.shutdown()
            except Exception:
                pass
