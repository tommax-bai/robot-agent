"""任务提交与取消。路径保持兼容：/agent/actions, /agent/actions/sync, /agent/cancel。"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Body

import utils.logger as logger
from agents.base import Task
from api.v1._common import resolve_worker
from api.v1.schemas import TaskSubmitReq
from runtime import Host
from utils.events import Ev, ev

router = APIRouter()


async def _run_background(trace_id: str, req: TaskSubmitReq) -> None:
    """异步执行任务，异常仅记录不向上抛。`task.*` 由 Supervisor 独家发布。"""
    try:
        worker = resolve_worker(req.account_id)
        ev(Ev.API_TASK_RECEIVED, trace_id=trace_id, account_id=worker.account_id,
           goal=req.user_goal, mode="async")
        task = Task(kind="user_goal", goal=req.user_goal)
        await worker.supervisor.submit_task(task, trace_id=trace_id)
    except Exception as e:
        # Supervisor 已发 task.failed；这里只捕获 submit 之前的路由异常。
        logger.error({"msg": "Agent 任务提交失败", "error": str(e)}, trace_id)


@router.post("/agent/actions")
async def submit_async(req: TaskSubmitReq = Body(...)):
    """异步启动 Agent 任务，立即返回 trace_id。"""
    trace_id = str(uuid.uuid4())
    asyncio.create_task(_run_background(trace_id, req))
    return {
        "trace_id": trace_id,
        "account_id": req.account_id or Host.current().default_id,
        "message": "任务已启动，正在后台执行",
        "result": None,
        "error": None,
    }


@router.post("/agent/actions/sync")
async def submit_sync(req: TaskSubmitReq = Body(...)):
    """同步执行：阻塞至任务完成，返回完整结果。"""
    trace_id = str(uuid.uuid4())
    try:
        worker = resolve_worker(req.account_id)
        ev(Ev.API_TASK_RECEIVED, trace_id=trace_id, account_id=worker.account_id,
           goal=req.user_goal, mode="sync")
        task = Task(kind="user_goal", goal=req.user_goal)
        result = await worker.supervisor.submit_task(task, trace_id=trace_id)
        return {
            "trace_id": trace_id,
            "account_id": worker.account_id,
            "message": "任务完成" if result.ok else "任务失败",
            "result": {
                "ok": result.ok,
                "summary": result.summary,
                "error": str(result.error) if result.error else None,
            },
            "error": None,
        }
    except Exception as e:
        logger.error({"msg": "Agent 任务执行失败", "error": str(e)}, trace_id)
        return {
            "trace_id": trace_id,
            "message": "任务失败",
            "result": None,
            "error": str(e),
        }


@router.post("/agent/cancel")
async def cancel(account_id: str | None = Body(None, embed=True)):
    """请求取消指定 worker 的当前任务（非阻塞）。"""
    worker = resolve_worker(account_id)
    worker.supervisor.request_stop("dashboard_cancel")
    return {"message": f"已请求取消 worker={worker.account_id} 的当前任务"}
