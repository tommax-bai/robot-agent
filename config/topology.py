"""进程拓扑：一个进程可以是 monolith、brain-only、session-only。

由 AGENT_TOPOLOGY 环境变量选择，默认 monolith。
替代旧的三个入口文件（app.py / app_brain.py / app_session.py）——
现在只有一个 app.py，根据 topology 决定挂载哪些路由、启动哪些服务。
"""
from __future__ import annotations

from enum import StrEnum


class Topology(StrEnum):
    MONOLITH = "monolith"
    BRAIN = "brain"
    SESSION = "session"

    @property
    def has_brain(self) -> bool:
        """是否装配决策层（Supervisor/Operator/Planner/...)。"""
        return self in (Topology.MONOLITH, Topology.BRAIN)

    @property
    def has_session(self) -> bool:
        """是否管理云手机 session（screenshot/action/delegate）。"""
        return self in (Topology.MONOLITH, Topology.SESSION)

    @property
    def needs_local_chrome(self) -> bool:
        """是否需要 bootstrap 本地 Chrome（仅 monolith 本地模式用）。"""
        return self == Topology.MONOLITH

    @property
    def serves_dashboard(self) -> bool:
        """是否暴露 dashboard 和健康相关 UI。"""
        return self != Topology.BRAIN  # brain 通常内部服务，不对外
