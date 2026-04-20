"""路由内部的复用函数：worker 解析、runtime payload 聚合。"""
from __future__ import annotations

from fastapi import HTTPException

from runtime import Host


def resolve_worker(account_id: str | None = None):
    """根据可选 account_id 取 Worker；省略即 default。未注册 id 抛 404。"""
    host = Host.current()
    if not account_id:
        if not host.workers:
            raise HTTPException(status_code=503, detail="Host 中没有任何 worker")
        return host.default()
    if not host.has(account_id):
        raise HTTPException(status_code=404, detail=f"未注册的 account_id={account_id}")
    return host.worker(account_id)


def worker_runtime_payload(worker) -> dict:
    """聚合一个 worker 的 runtime 视图（mode / env / strategy / session）。"""
    from tools.wuying_cloud.session import SessionState

    payload: dict = {
        "account_id": worker.account_id,
        "mode": worker.mode,
        "env": type(worker.env).__name__,
    }
    if worker.strategy is not None:
        payload["strategy"] = {
            "type": type(worker.strategy).__name__,
            "name": worker.strategy.name,
        }
    sm = worker.session_mgr
    if sm is not None:
        sw, sh = sm.screen_size
        payload["agentbay"] = {
            "image_id": sm.image_id,
            "platform": sm.platform,  # "mobile" | "desktop"
            "session_state": sm.state.value,
            "session_active": sm.state == SessionState.ACTIVE,
            "session_id": sm.session_id,
            "screen_size": {"width": sw, "height": sh},
            "live_view_url": sm.get_url(),
        }
    return payload


__all__ = ["resolve_worker", "worker_runtime_payload"]
