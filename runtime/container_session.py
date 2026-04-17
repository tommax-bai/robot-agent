"""
SessionAppContainer: Session Service 的依赖注入容器。

只管 SessionManager + AgentBayBackend（云手机生命周期），
不含 LLM / Planner / Supervisor 等决策层。
"""

from __future__ import annotations

import config
from runtime.event_bus import EventBus
from runtime.session_worker import SessionWorker
from tools.backends.agentbay import AgentBayBackend, cleanup_orphan_sessions
from tools.backends.session_manager import SessionManager


class SessionAppContainer:
    """Session Service 级单例。"""

    def __init__(self):
        self.event_bus = EventBus()
        self.workers: dict[str, SessionWorker] = {}

        runtime_cfg = config.agent["runtime"]
        ab = runtime_cfg["agentbay"]

        # 启动孤儿清理
        if runtime_cfg.get("auto_cleanup_orphans_on_startup") and ab.get("api_key"):
            cleanup_orphan_sessions(ab["api_key"])

        # 按 accounts 配置创建 session worker
        accounts = config.agent.get("accounts") or []
        if accounts:
            for acct in accounts:
                self.add_worker(acct)
        else:
            self.add_worker(None)

    def add_worker(self, account_cfg: dict | None) -> SessionWorker:
        account_id = (account_cfg or {}).get("id") or "default"
        ab = config.agent["runtime"]["agentbay"]
        cfg = account_cfg or {}

        session_mgr = SessionManager(
            api_key=ab["api_key"],
            image_id=cfg.get("image_id") or ab["image_id"],
            idle_release_timeout=ab.get("idle_release_timeout"),
            mode=cfg.get("mode") or config.agent["runtime"]["mode"],
            event_bus=self.event_bus,
            account_id=account_id,
        )
        backend = AgentBayBackend(
            session_mgr=session_mgr,
            screenshot_format=cfg.get("screenshot_format") or ab["screenshot_format"],
            mode=cfg.get("mode") or config.agent["runtime"]["mode"],
        )
        worker = SessionWorker(
            account_id=account_id,
            session_mgr=session_mgr,
            backend=backend,
            display_name=cfg.get("display_name", ""),
        )
        self.workers[account_id] = worker
        return worker

    def remove_worker(self, account_id: str) -> SessionWorker:
        if account_id not in self.workers:
            raise KeyError(f"未注册的 account_id={account_id}")
        if len(self.workers) <= 1:
            raise RuntimeError("不能移除最后一个 worker")
        worker = self.workers.pop(account_id)
        worker.shutdown()
        return worker

    def get(self, account_id: str) -> SessionWorker:
        if account_id not in self.workers:
            raise KeyError(f"未注册的 account_id={account_id}")
        return self.workers[account_id]

    def default(self) -> SessionWorker:
        return next(iter(self.workers.values()))

    def list(self) -> list[SessionWorker]:
        return list(self.workers.values())

    def shutdown_all(self) -> None:
        for w in self.workers.values():
            w.shutdown()


# 模块级单例
_container: SessionAppContainer | None = None


def get_session_container() -> SessionAppContainer:
    if _container is None:
        raise RuntimeError("SessionAppContainer 尚未初始化")
    return _container


def init_session_container() -> SessionAppContainer:
    global _container
    if _container is None:
        _container = SessionAppContainer()
    return _container
