"""Session Service API：供 Brain Service 远程调用的截图/动作/委托接口。"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException

import utils.logger as logger
from dto.session import (
    ActionRequest,
    DelegateTaskRequest,
    ScreenshotRequest,
    SessionAcquireRequest,
    SessionReleaseRequest,
    WorkerCreateRequest,
)
from runtime.container_session import get_session_container
from tools.backends.session_manager import SessionState

router = APIRouter()


def _resolve(account_id: str):
    c = get_session_container()
    aid = account_id or next(iter(c.workers)).account_id if c.workers else "default"
    try:
        return c.get(aid)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未注册的 account_id={aid}")


# ── Session 生命周期 ──────────────────────────────────────────

@router.post("/session/acquire")
async def acquire_session(req: SessionAcquireRequest):
    w = _resolve(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        await asyncio.to_thread(w.session_mgr.acquire, trace)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    sw, sh = w.session_mgr.screen_size
    return {
        "ok": True,
        "session_id": w.session_mgr.session_id,
        "live_view_url": w.session_mgr.get_url(),
        "screen_size": {"width": sw, "height": sh},
    }


@router.post("/session/release")
async def release_session(req: SessionReleaseRequest):
    w = _resolve(req.account_id)
    try:
        await asyncio.to_thread(w.session_mgr.release)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "message": "session released"}


@router.get("/session/status")
async def session_status(account_id: str = "default"):
    w = _resolve(account_id)
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
async def session_url(account_id: str = "default"):
    w = _resolve(account_id)
    url = await asyncio.to_thread(w.session_mgr.refresh_url)
    return {"live_view_url": url, "refreshed": bool(url)}


# ── ActionBackend over HTTP ───────────────────────────────────

@router.post("/session/screenshot")
async def screenshot(req: ScreenshotRequest):
    w = _resolve(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        b64, cx, cy = await asyncio.to_thread(
            w.backend.screenshot, trace, req.include_cursor
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "base64": "", "cursor_x": 0, "cursor_y": 0}
    return {"ok": True, "base64": b64, "cursor_x": cx, "cursor_y": cy}


@router.post("/session/action")
async def action(req: ActionRequest):
    w = _resolve(req.account_id)
    trace = req.trace_id or str(uuid.uuid4())
    try:
        result = await asyncio.to_thread(
            w.backend.execute_action, trace, req.action
        )
    except Exception as e:
        return {"ok": False, "message": str(e), "finish": False}
    return result


# ── 委托执行（agentbay 模式）──────────────────────────────────

@router.post("/session/delegate-task")
async def delegate_task(req: DelegateTaskRequest):
    """长时间运行：把 goal 交给 AgentBay mobile_use Agent，等结果返回。"""
    w = _resolve(req.account_id)
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
        exec_handle = await asyncio.to_thread(
            mobile_agent.execute_task,
            req.goal, req.max_steps, None, _on_content, _on_tool_call,
        )
        result = await asyncio.to_thread(exec_handle.wait, req.timeout_seconds)
    except Exception as e:
        logger.error({"msg": "delegate-task 异常", "error": str(e)}, trace)
        from tools.backends.session_manager import is_session_dead_error
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


# ── Worker 管理 ───────────────────────────────────────────────

@router.get("/session/workers")
async def list_workers():
    c = get_session_container()
    out = []
    for w in c.list():
        sm = w.session_mgr
        sw, sh = sm.screen_size
        out.append({
            "account_id": w.account_id,
            "display_name": w.display_name,
            "state": sm.state.value,
            "session_id": sm.session_id,
            "screen_size": {"width": sw, "height": sh},
            "live_view_url": sm.get_url(),
        })
    return {"workers": out}


@router.post("/session/workers")
async def create_worker(req: WorkerCreateRequest):
    c = get_session_container()
    account_id = req.account_id or f"worker-{uuid.uuid4().hex[:8]}"
    if account_id in c.workers:
        raise HTTPException(status_code=409, detail=f"account_id={account_id} 已存在")
    cfg = {"id": account_id}
    if req.image_id:
        cfg["image_id"] = req.image_id
    c.add_worker(cfg)
    return {"ok": True, "account_id": account_id}


@router.delete("/session/workers/{account_id}")
async def remove_worker(account_id: str):
    c = get_session_container()
    try:
        c.remove_worker(account_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "account_id": account_id}


# ── SSE 事件流 ────────────────────────────────────────────────

from fastapi import Request
from fastapi.responses import StreamingResponse


@router.get("/session/events")
async def session_events(request: Request):
    import json
    import time

    bus = get_session_container().event_bus
    queue = bus.subscribe()

    async def stream():
        try:
            yield f"event: ready\ndata: {{}}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                if await request.is_disconnected():
                    break
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── 健康检查 ──────────────────────────────────────────────────

@router.get("/session/config")
async def session_config():
    """Dashboard 启动时拉取：Brain Service 地址等配置。"""
    import os
    brain_url = os.getenv("BRAIN_SERVICE_URL", "")
    return {"brain_url": brain_url}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "session"}
