"""
SessionWorker: Session Service 侧的 per-account 执行单元。

只持有 SessionManager + AgentBayBackend，不含任何决策逻辑（Supervisor/Operator/Planner 等在 Brain Service）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.backends.agentbay import AgentBayBackend
from tools.backends.session_manager import SessionManager

if TYPE_CHECKING:
    from runtime.event_bus import EventBus


class SessionWorker:
    """Session Service 侧的 worker：一台云手机 = 一个 SessionWorker。"""

    def __init__(
        self,
        *,
        account_id: str,
        session_mgr: SessionManager,
        backend: AgentBayBackend,
        display_name: str = "",
    ):
        self.account_id = account_id
        self.session_mgr = session_mgr
        self.backend = backend
        self.display_name = display_name or account_id

    def shutdown(self) -> None:
        try:
            self.session_mgr.release()
        except Exception:
            pass
