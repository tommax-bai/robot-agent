"""Session Service 对外 API：供 Brain Service 远程调用的截图/动作/委托接口。

只在 `topology in {session, monolith}` 时挂载（monolith 下主要给本进程自洽用，
生产中一般不会同时拉起它，但挂着也不会有副作用）。
"""
from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import APIRouter, HTTPException

import utils.logger as logger
from api.v1._common import resolve_worker
from api.v1.schemas import (
    ActionReq,
    DelegateTaskReq,
    ScreenshotReq,
    SessionAcquireReq,
    SessionReleaseReq,
    WorkerCreateReq,
)
from runtime import Host

router = APIRouter()


# ── Session 生命周期 ──────────────────────────────────────────

@router.post("/session/acquire")
async def acquire(req: SessionAcquireReq):
    w = resolve_worker(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        await asyncio.to_thread(w.session_mgr.acquire, trace)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    sw, sh = w.session_mgr.screen_size
    return {
        "ok": True,
        "session_id": w.session_mgr.session_id,
        "live_view_url": w.session_mgr.get_url(),
        "screen_size": {"width": sw, "height": sh},
    }


@router.post("/session/release")
async def release(req: SessionReleaseReq):
    w = resolve_worker(req.account_id)
    try:
        await asyncio.to_thread(w.session_mgr.release)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "message": "session released"}


@router.get("/session/status")
async def status(account_id: str = "default"):
    w = resolve_worker(account_id)
    sm = w.session_mgr
    sw, sh = sm.screen_size
    return {
        "account_id": w.account_id,
        "state": sm.state.value,
        "session_id": sm.session_id,
        "screen_size": {"width": sw, "height": sh},
        "live_view_url": sm.get_url(),
    }


@router.get("/session/url")
async def url(account_id: str = "default"):
    w = resolve_worker(account_id)
    live = await asyncio.to_thread(w.session_mgr.refresh_url)
    return {"live_view_url": live, "refreshed": bool(live)}


# ── Environment over HTTP ─────────────────────────────────────

@router.post("/session/screenshot")
async def screenshot(req: ScreenshotReq):
    w = resolve_worker(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        b64, cx, cy = await asyncio.to_thread(w.env.capture, trace, req.include_cursor)
    except Exception as e:
        return {"ok": False, "error": str(e), "base64": "", "cursor_x": 0, "cursor_y": 0}
    return {"ok": True, "base64": b64, "cursor_x": cx, "cursor_y": cy}


@router.post("/session/action")
async def action(req: ActionReq):
    w = resolve_worker(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        result = await asyncio.to_thread(w.env.perform, trace, req.action)
    except Exception as e:
        return {"ok": False, "message": str(e), "finish": False}
    return result


# ── 委托执行（agentbay 模式）──────────────────────────────────

@router.post("/session/delegate-task")
async def delegate_task(req: DelegateTaskReq):
    w = resolve_worker(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())

    session = await asyncio.to_thread(w.session_mgr.acquire, trace)
    mobile_agent = session.agent.mobile

    content_buf: list[str] = []
    tool_calls: list[dict] = []

    def _on_content(event):
        text = getattr(event, "content", "") or ""
        if text:
            content_buf.append(text)

    def _on_tool_call(event):
        tool_calls.append({
            "tool": getattr(event, "tool_name", ""),
            "args": getattr(event, "args", {}) or {},
        })

    try:
        handle = await asyncio.to_thread(
            mobile_agent.execute_task,
            req.goal, req.max_steps, None, _on_content, _on_tool_call,
        )
        result = await asyncio.to_thread(handle.wait, req.timeout_seconds)
    except Exception as e:
        logger.error({"msg": "delegate-task 异常", "error": str(e)}, trace)
        from tools.cloud_mobile.session import is_session_dead_error
        if is_session_dead_error(e):
            w.session_mgr.mark_dead("delegate-task", str(e), trace)
        return {"ok": False, "error": str(e), "summary": ""}

    ok = bool(getattr(result, "success", False))
    error_msg = getattr(result, "error_message", "") or ""

    notes = [
        str(c["args"].get("text", "")).strip()
        for c in tool_calls
        if c["tool"] == "take_note" and c["args"].get("text")
    ]
    agent_text = "".join(content_buf).strip()
    sections: list[str] = []
    if notes:
        sections.append("【观察结果】\n" + "\n\n".join(notes))
    if agent_text:
        sections.append("【执行过程】\n" + agent_text)
    summary = "\n\n".join(sections) or (getattr(result, "task_result", None) or "")

    return {
        "ok": ok,
        "summary": summary,
        "error": error_msg,
        "tool_calls": len(tool_calls),
        "notes": len(notes),
    }


# ── Worker 管理（session 视角）────────────────────────────────

@router.get("/session/workers")
async def list_workers():
    host = Host.current()
    out = []
    for w in host.list():
        sm = w.session_mgr
        sw, sh = sm.screen_size if sm else (0, 0)
        out.append({
            "account_id": w.account_id,
            "display_name": w.display_name,
            "state": sm.state.value if sm else "none",
            "session_id": sm.session_id if sm else "",
            "screen_size": {"width": sw, "height": sh},
            "live_view_url": sm.get_url() if sm else "",
        })
    return {"workers": out}


@router.post("/session/workers")
async def create_worker(req: WorkerCreateReq):
    host = Host.current()
    account_id = req.account_id or f"worker-{uuid.uuid4().hex[:8]}"
    if account_id in host.workers:
        raise HTTPException(status_code=409, detail=f"account_id={account_id} 已存在")
    cfg: dict = {"id": account_id}
    if req.image_id:
        cfg["image_id"] = req.image_id
    host.add_worker(cfg, start_scheduler=False)
    return {"ok": True, "account_id": account_id}


@router.delete("/session/workers/{account_id}")
async def remove_worker(account_id: str):
    host = Host.current()
    try:
        host.remove_worker(account_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True, "account_id": account_id}


# ── 配置探测（dashboard 启动时拉）───────────────────────────

@router.get("/session/config")
async def session_config():
    """Dashboard 启动时拉取：判断是否需要 cross-origin fetch Brain Service。"""
    return {"brain_url": os.getenv("BRAIN_SERVICE_URL", "")}


@router.get("/session/health")
async def session_health():
    return {"status": "ok", "service": "session"}
