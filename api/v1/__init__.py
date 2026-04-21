"""API v1 的注册入口。按 topology 挂载相应的 router。

Brain 拓扑挂：tasks / workers / events / callbacks / usage / chrome_proxy
Session 拓扑挂：sessions / events
Monolith（默认）挂全部。

URL 路径保持与旧 agent.py / session_api.py 一致，便于 dashboard 和外部脚本不改。
"""
from __future__ import annotations

from fastapi import FastAPI

from config.topology import Topology

API_PREFIX = "/api/v1"


def register_routers(app: FastAPI, topology: Topology) -> None:
    """根据 topology 挂载路由。"""
    # events 各拓扑都要（dashboard SSE）
    from api.v1 import events
    app.include_router(events.router, prefix=API_PREFIX, tags=["Events"])

    if topology.has_brain:
        from api.v1 import callbacks, chrome_proxy, data, tasks, usage, workers

        app.include_router(tasks.router, prefix=API_PREFIX, tags=["Tasks"])
        app.include_router(workers.router, prefix=API_PREFIX, tags=["Workers"])
        app.include_router(callbacks.router, prefix=API_PREFIX, tags=["Callback"])
        app.include_router(usage.router, prefix=API_PREFIX, tags=["Usage"])
        app.include_router(data.router, prefix=API_PREFIX, tags=["Data"])
        # chrome 代理仅本地 chrome 才有意义，但 brain 拓扑下也允许（不活跃即不会被调）
        app.include_router(chrome_proxy.router, prefix=API_PREFIX, tags=["ChromeProxy"])

    if topology.has_session:
        from api.v1 import sessions
        app.include_router(sessions.router, prefix=API_PREFIX, tags=["Session"])


__all__ = ["API_PREFIX", "register_routers"]
