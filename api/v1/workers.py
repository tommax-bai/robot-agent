"""Worker 相关：列表/新增/删除 + 状态/runtime/模式切换 + 截图/live-url/release/patrol。

URL 保持与旧 `agent.py` 一致，内部按 HTTP 方法与资源分组。
"""
from __future__ import annotations

import asyncio
import base64
import uuid

from fastapi import APIRouter, Body, HTTPException, Response

import utils.logger as logger
from agents.supervisor import AgentMode
from api.v1._common import resolve_worker, worker_runtime_payload
from runtime import Host

router = APIRouter()


# ── Worker CRUD ───────────────────────────────────────────────

@router.get("/agent/workers")
async def list_workers():
    host = Host.current()
    out = []
    for w in host.list():
        runtime = worker_runtime_payload(w)
        status_dict: dict = {}
        if w.supervisor is not None:
            status_dict = w.supervisor.status().as_dict()
        out.append({
            **runtime,
            "display_name": w.account_cfg.get("display_name") or w.account_id,
            "description": w.account_cfg.get("description", ""),
            "is_default": w.account_id == host.default_id,
            "supervisor": status_dict,
        })
    return {"workers": out, "default_id": host.default_id}


@router.post("/agent/workers")
async def create_workers(
    id: str | None = Body(None, embed=True),
    count: int = Body(1, embed=True),
    display_name: str | None = Body(None, embed=True),
    image_id: str | None = Body(None, embed=True),
):
    """运行时新增 N 个 worker（不写回 config，重启即消失）。"""
    if count < 1 or count > 10:
        raise HTTPException(status_code=400, detail="count 必须在 1..10 范围内")
    if count > 1 and id:
        raise HTTPException(status_code=400, detail="count > 1 时 id 必须留空（自动生成）")

    host = Host.current()
    created: list[dict] = []
    for _ in range(count):
        acct_id = id or f"worker-{uuid.uuid4().hex[:8]}"
        cfg: dict = {"id": acct_id}
        if display_name:
            cfg["display_name"] = display_name
        if image_id:
            cfg["image_id"] = image_id
        try:
            worker = host.add_worker(cfg)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except Exception as e:
            logger.error({"msg": "新增 worker 失败", "id": acct_id, "error": str(e)})
            raise HTTPException(status_code=500, detail=f"新增失败: {e}") from e
        created.append({
            "account_id": worker.account_id,
            "display_name": worker.account_cfg.get("display_name") or worker.account_id,
            "env": type(worker.env).__name__,
        })
    return {"created": created, "count": len(created)}


@router.delete("/agent/workers/{account_id}")
async def remove_worker(account_id: str):
    """停 scheduler、释放 session、从池里删。"""
    try:
        worker = Host.current().remove_worker(account_id)
        return {
            "ok": True,
            "removed": worker.account_id,
            "message": f"worker={account_id} 已移除，session 已释放",
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.error({"msg": "移除 worker 失败", "id": account_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"移除失败: {e}") from e


# ── 状态与 runtime 视图 ───────────────────────────────────────

@router.get("/agent/status")
async def status(account_id: str | None = None):
    """单 worker 状态。"""
    worker = resolve_worker(account_id)
    if worker.supervisor is None:
        raise HTTPException(status_code=404, detail="minimal worker 没有 supervisor")
    return worker.supervisor.status().as_dict()


@router.get("/agent/runtime")
async def runtime_view(account_id: str | None = None):
    """单 worker runtime 视图。多 worker 聚合请用 /agent/workers。"""
    return worker_runtime_payload(resolve_worker(account_id))


@router.post("/agent/runtime/mode")
async def set_runtime_mode(
    mode: str = Body(..., embed=True),
    account_id: str | None = Body(None, embed=True),
):
    """热切换某 worker 的 runtime.mode（backend + 策略）。"""
    worker = resolve_worker(account_id)
    try:
        return await worker.swap_runtime_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error({"msg": "runtime.mode 切换失败", "error": str(e)})
        raise HTTPException(status_code=500, detail=f"切换失败: {e}") from e


# ── 串流 / 截图 ───────────────────────────────────────────────

@router.get("/agent/live-url")
async def live_url(account_id: str | None = None):
    """获取云手机串流地址（authCode 快过期时自动刷新）。"""
    worker = resolve_worker(account_id)
    sm = worker.session_mgr
    if sm is None:
        return {"live_view_url": "", "refreshed": False}
    url = await asyncio.to_thread(sm.refresh_url)
    return {"live_view_url": url, "refreshed": bool(url)}


@router.get("/agent/screen.jpg")
async def screen(account_id: str | None = None):
    """指定 worker 的本地 Chrome 截图（仅 local_chrome 模式）。"""
    worker = resolve_worker(account_id)
    if worker.mode != "local_chrome":
        raise HTTPException(
            status_code=404,
            detail=f"screen.jpg 仅在 local_chrome 模式可用（当前 {worker.mode}），云端请用 live_view_url",
        )
    try:
        b64, _, _ = worker.env.capture("dashboard", include_cursor=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"截图失败: {e}") from e
    if not b64:
        raise HTTPException(status_code=503, detail="截图为空（窗口未找到？）")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg")


# ── Session release ───────────────────────────────────────────

@router.post("/agent/session/release")
async def release_session(
    account_id: str | None = Body(None, embed=True),
    force: bool = Body(False, embed=True),
):
    """主动释放 worker 的云端 session（停计费、登录态丢失）。"""
    worker = resolve_worker(account_id)
    try:
        return worker.release_session(force=force)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.error({"msg": "release_session 失败", "account": worker.account_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"释放失败: {e}") from e


# ── Supervisor 模式切换 ───────────────────────────────────────

@router.post("/agent/mode/debug")
async def set_debug_mode(account_id: str | None = Body(None, embed=True)):
    worker = resolve_worker(account_id)
    worker.supervisor.switch_mode(AgentMode.DEBUG)
    return {"message": f"worker={worker.account_id} 已进入调试模式"}


@router.post("/agent/mode/waiting")
async def set_waiting_mode(account_id: str | None = Body(None, embed=True)):
    worker = resolve_worker(account_id)
    worker.supervisor.switch_mode(AgentMode.WAITING)
    return {"message": f"worker={worker.account_id} 已进入 WAITING 模式"}


@router.post("/agent/patrol")
async def toggle_patrol(
    enable: bool = Body(..., embed=True),
    account_id: str | None = Body(None, embed=True),
):
    """开/关指定 worker 的自动巡逻调度。"""
    worker = resolve_worker(account_id)
    sv = worker.supervisor
    if enable:
        sv.switch_mode(AgentMode.PATROLLING)
        sv.start()
        return {"message": f"worker={worker.account_id} 自动调度已开启"}
    sv.switch_mode(AgentMode.WAITING)
    return {"message": f"worker={worker.account_id} 自动调度已关闭"}


# ── 手动触发一次 patrol ───────────────────────────────────────

@router.post("/agent/scheduled/patrol-once")
async def patrol_once(account_id: str | None = Body(None, embed=True)):
    """按 persona 手动触发一次 patrol。"""
    worker = resolve_worker(account_id)
    sv = worker.supervisor
    if sv.mode == AgentMode.PATROLLING:
        raise HTTPException(
            status_code=409,
            detail=f"worker={worker.account_id} 的 patrol 自动调度正在运行，请先关闭再手动触发",
        )
    trace_id = f"patrol-manual-{uuid.uuid4().hex[:8]}"
    asyncio.create_task(_patrol_bg(sv, trace_id))
    return {
        "trace_id": trace_id,
        "account_id": worker.account_id,
        "message": "已触发人设驱动的一次 patrol，详情看 logs 与 live view",
    }


async def _patrol_bg(sv, trace_id: str) -> None:
    try:
        result = await sv.trigger_patrol(trace_id=trace_id)
        logger.info({"msg": "人设驱动 patrol 完成", "ok": result.ok}, trace_id)
    except RuntimeError as e:
        logger.warning({"msg": "人设驱动 patrol 被拒绝（运行期 race）", "reason": str(e)}, trace_id)
    except Exception as e:
        logger.error({"msg": "人设驱动 patrol 异常", "error": str(e)}, trace_id)
