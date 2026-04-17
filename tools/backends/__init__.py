"""
执行后端（Backend）抽象层。

Backend 把"在哪执行原子动作 + 在哪截屏"封装成一个接口，
让上层 agent 与具体环境（本地 macOS Chrome / 阿里 AgentBay 云手机 / ...）解耦。

当前接口签名故意与原 tools.actions / tools.screenshot 的模块级函数保持一致，
便于零行为变更地把现有调用迁移到 backend；后续新增的 tap/swipe/keyevent
等更原生的能力会作为 ActionBackend 的扩展方法逐步加入。
"""

from __future__ import annotations

from typing import Protocol


class ActionBackend(Protocol):
    """执行后端协议。

    实现类负责：
    - screenshot: 截屏并返回 base64 编码图像（供 VLM 输入）
    - execute_action: 执行一条原子动作（click/scroll/paste/...），返回结构化结果
    """

    def screenshot(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        """截屏。

        Returns:
            (base64 图像, 鼠标 x, 鼠标 y)。include_cursor=False 时坐标可为 0。
        """
        ...

    def execute_action(self, trace_id: str, action: dict) -> dict:
        """执行一条原子动作。

        Args:
            trace_id: 追踪 ID。
            action: 形如 {"method": "click", "params": {...}, "finish": False}。

        Returns:
            形如 {"ok": bool, "message": str, "finish": bool, ...} 的结果 dict。
        """
        ...


from tools.backends.agentbay import AgentBayBackend, cleanup_orphan_sessions  # noqa: E402
from tools.backends.macos_chrome import MacOSChromeBackend  # noqa: E402
from tools.backends.remote import RemoteBackend  # noqa: E402
from tools.backends.remote_delegate import RemoteDelegateStrategy  # noqa: E402
from tools.backends.session_manager import SessionManager, SessionState, is_session_dead_error  # noqa: E402

__all__ = [
    "ActionBackend",
    "AgentBayBackend",
    "MacOSChromeBackend",
    "cleanup_orphan_sessions",
    "SessionManager",
    "SessionState",
    "is_session_dead_error",
]
