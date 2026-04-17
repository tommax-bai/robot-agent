from __future__ import annotations

import asyncio
import json
import uuid
from io import BytesIO
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Body, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

import config
import utils.logger as logger
from agents.base import Task
from agents.supervisor import AgentMode
from dto.agent import AgentRequest
from runtime.container import get_container

router = APIRouter()


def _resolve_worker(account_id: str | None = None):
    """根据可选 account_id 取 Worker。省略 → default worker。
    未注册的 id 返回 404。"""
    pool = get_container().workers
    if not account_id:
        return pool.default()
    if not pool.has(account_id):
        raise HTTPException(status_code=404, detail=f"未注册的 account_id={account_id}")
    return pool.get(account_id)


def _backend_runtime_payload(worker) -> dict:
    """聚合一个 worker 的 runtime 视图（mode / backend / strategy / session）。"""
    from tools.backends.session_manager import SessionState

    payload: dict = {
        "account_id": worker.account_id,
        "mode": worker.mode,
        "backend": type(worker.backend).__name__,
        "strategy": {"type": type(worker.strategy).__name__, "name": worker.strategy.name},
    }
    sm = worker.session_mgr
    if sm is not None:
        sw, sh = sm.screen_size
        payload["agentbay"] = {
            "image_id": sm.image_id,
            "session_state": sm.state.value,
            "session_active": sm.state == SessionState.ACTIVE,
            "session_id": sm.session_id,
            "screen_size": {"width": sw, "height": sh},
            "live_view_url": sm.get_url(),
        }
    return payload


def _supervisor():
    """向后兼容：原来的 _supervisor() 入口，等价于 default worker 的 supervisor。"""
    return _resolve_worker(None).supervisor


async def handle_agent_actions(trace_id: str, request: AgentRequest):
    """异步执行 Agent 任务（路由到指定 worker，或 default）"""
    try:
        worker = _resolve_worker(request.account_id)
        logger.info(
            {"msg": "开始执行 Agent 任务", "user_goal": request.user_goal, "account": worker.account_id},
            trace_id,
        )
        task = Task(kind="user_goal", goal=request.user_goal)
        result = await worker.supervisor.execute_task(task, trace_id=trace_id)
        logger.info({"msg": "Agent 任务完成", "ok": result.ok, "account": worker.account_id}, trace_id)
    except Exception as e:
        logger.error({"msg": "Agent 任务执行失败", "error": str(e)}, trace_id)


@router.post("/agent/actions")
async def router_agent_actions(request: AgentRequest = Body(...)):
    """启动 Agent 执行任务（异步）"""
    trace_id = str(uuid.uuid4())
    asyncio.create_task(handle_agent_actions(trace_id, request))
    return {
        "trace_id": trace_id,
        "account_id": request.account_id or get_container().workers.default_id,
        "message": "任务已启动，正在后台执行",
        "result": None,
        "error": None,
    }


@router.post("/agent/actions/sync")
async def router_agent_actions_sync(request: AgentRequest = Body(...)):
    """同步执行 Agent 任务"""
    trace_id = str(uuid.uuid4())
    try:
        worker = _resolve_worker(request.account_id)
        task = Task(kind="user_goal", goal=request.user_goal)
        result = await worker.supervisor.execute_task(task, trace_id=trace_id)
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


@router.post("/agent/workers")
async def create_workers(
    id: str | None = Body(None, embed=True),
    count: int = Body(1, embed=True),
    display_name: str | None = Body(None, embed=True),
    image_id: str | None = Body(None, embed=True),
):
    """运行时新增 N 个 worker（不写回 config，重启即消失）。

    - id 留空 → 自动生成 worker-{8 位 hex}（count > 1 时强制自动生成，避免冲突）
    - count = 1..10
    - 同名冲突 → 409
    - image_id 缺省走全局 runtime.agentbay.image_id
    """
    if count < 1 or count > 10:
        raise HTTPException(status_code=400, detail="count 必须在 1..10 范围内")
    if count > 1 and id:
        raise HTTPException(status_code=400, detail="count > 1 时 id 必须留空（自动生成）")

    container = get_container()
    created: list[dict] = []
    for _ in range(count):
        acct_id = id or f"worker-{uuid.uuid4().hex[:8]}"
        cfg: dict = {"id": acct_id}
        if display_name:
            cfg["display_name"] = display_name
        if image_id:
            cfg["image_id"] = image_id
        try:
            worker = container.add_worker(cfg)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except Exception as e:
            logger.error({"msg": "新增 worker 失败", "id": acct_id, "error": str(e)})
            raise HTTPException(status_code=500, detail=f"新增失败: {e}") from e
        created.append(
            {
                "account_id": worker.account_id,
                "display_name": worker.account_cfg.get("display_name") or worker.account_id,
                "backend": type(worker.backend).__name__,
            }
        )
    return {"created": created, "count": len(created)}


@router.delete("/agent/workers/{account_id}")
async def remove_worker(account_id: str):
    """移除 worker：停 scheduler、释放 session、从池里删。

    - 不存在 → 404
    - 是最后一个 / 任务活跃 → 409
    """
    try:
        worker = get_container().remove_worker(account_id)
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


@router.get("/agent/workers")
async def list_workers():
    """列出所有 worker：账号 ID、display_name、runtime 信息、当前任务状态。

    Dashboard 用此端点渲染左侧 worker 卡片列表。
    """
    pool = get_container().workers
    out = []
    for w in pool.list():
        runtime = _backend_runtime_payload(w)
        status = w.supervisor.get_status()
        out.append(
            {
                **runtime,
                "display_name": w.account_cfg.get("display_name") or w.account_id,
                "description": w.account_cfg.get("description", ""),
                "is_default": w.account_id == pool.default_id,
                "supervisor": status,
            }
        )
    return {"workers": out, "default_id": pool.default_id}


@router.get("/agent/status")
async def get_agent_status(account_id: str | None = None):
    """单 worker 状态。account_id 省略 → default。"""
    return _resolve_worker(account_id).supervisor.get_status()


@router.get("/agent/runtime")
async def get_agent_runtime(account_id: str | None = None):
    """单 worker 的 runtime 视图。多 worker 聚合视图请用 /agent/workers。"""
    return _backend_runtime_payload(_resolve_worker(account_id))


@router.get("/agent/live-url")
async def get_live_url(account_id: str | None = None):
    """获取云手机串流地址（authCode 快过期时自动刷新）。"""
    worker = _resolve_worker(account_id)
    sm = worker.session_mgr
    if sm is None:
        return {"live_view_url": "", "refreshed": False}
    url = await asyncio.to_thread(sm.refresh_url)
    return {"live_view_url": url, "refreshed": bool(url)}


@router.post("/agent/runtime/mode")
async def set_runtime_mode(
    mode: str = Body(..., embed=True),
    account_id: str | None = Body(None, embed=True),
):
    """热切换某 worker 的 runtime.mode。"""
    try:
        worker = _resolve_worker(account_id)
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


@router.get("/agent/events")
async def agent_events(request: Request):
    """SSE 事件流：dashboard 订阅 supervisor 状态变化（mode 切换、任务起止）。

    客户端 EventSource('/api/v1/agent/events') 即可连接。
    每 25s 发一次 keepalive 注释，防止反向代理把 idle 连接断掉。
    """
    bus = get_container().event_bus
    queue = bus.subscribe()

    async def event_stream():
        try:
            # 连接建立时先推一条 ready 事件，前端可以立即触发一次状态拉取
            yield f"event: ready\ndata: {{}}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # SSE keepalive 注释行
                    yield ": ka\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲
            "Connection": "keep-alive",
        },
    )


@router.get("/agent/screen.jpg")
async def agent_screen(account_id: str | None = None):
    """指定 worker 的本地 Chrome 截图（仅 local_chrome 模式）。"""
    worker = _resolve_worker(account_id)
    runtime_mode = worker.mode  # per-worker
    if runtime_mode != "local_chrome":
        raise HTTPException(
            status_code=404,
            detail=f"screen.jpg 仅在 local_chrome 模式可用（当前 {runtime_mode}），云端请用 live_view_url",
        )
    try:
        b64, _, _ = worker.backend.screenshot("dashboard", include_cursor=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"截图失败: {e}") from e
    if not b64:
        raise HTTPException(status_code=503, detail="截图为空（窗口未找到？）")
    import base64

    return Response(content=base64.b64decode(b64), media_type="image/jpeg")


@router.post("/agent/cancel")
async def agent_cancel(account_id: str | None = Body(None, embed=True)):
    """请求取消指定 worker 的当前任务（非阻塞）。"""
    worker = _resolve_worker(account_id)
    worker.supervisor.request_cancel("dashboard_cancel")
    return {"message": f"已请求取消 worker={worker.account_id} 的当前任务"}


@router.post("/agent/session/release")
async def release_session(
    account_id: str | None = Body(None, embed=True),
    force: bool = Body(False, embed=True),
):
    """主动释放指定 worker 的云端 session（停计费、关闭浏览器等所有应用）。

    - force=False（默认）：任务活跃时 409 拒绝，需先 /agent/cancel
    - force=True：任务活跃时先 cancel 当前任务再 teardown（可能让正在执行的 SDK 调用报错，但能确保关闭）
    - 本地模式无 session 概念，返回 noop
    - 释放后下次发任务自动新建 session，需重新扫码登录
    """
    worker = _resolve_worker(account_id)
    try:
        return worker.release_session(force=force)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.error({"msg": "release_session 失败", "account": worker.account_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"释放失败: {e}") from e


@router.post("/agent/scheduled/patrol-once")
async def run_patrol_once_endpoint(account_id: str | None = Body(None, embed=True)):
    """按 persona 手动触发一次 patrol（指定 worker，或 default）。"""
    worker = _resolve_worker(account_id)
    sv = worker.supervisor
    if sv.mode == AgentMode.PATROLLING:
        raise HTTPException(
            status_code=409,
            detail=f"worker={worker.account_id} 的 patrol 自动调度正在运行，请先关闭再手动触发",
        )
    trace_id = f"patrol-manual-{uuid.uuid4().hex[:8]}"
    asyncio.create_task(_run_patrol_once_bg(sv, trace_id))
    return {
        "trace_id": trace_id,
        "account_id": worker.account_id,
        "message": "已触发人设驱动的一次 patrol，详情看 logs 与 live view",
    }


async def _run_patrol_once_bg(sv, trace_id: str) -> None:
    """后台 runner：吞掉异常避免 'Task exception was never retrieved' warning。"""
    try:
        result = await sv.execute_patrol_once(trace_id=trace_id)
        logger.info({"msg": "人设驱动 patrol 完成", "ok": result.ok}, trace_id)
    except RuntimeError as e:
        logger.warning({"msg": "人设驱动 patrol 被拒绝（运行期 race）", "reason": str(e)}, trace_id)
    except Exception as e:
        logger.error({"msg": "人设驱动 patrol 异常", "error": str(e)}, trace_id)


@router.post("/agent/patrol")
async def toggle_patrol(
    enable: bool = Body(..., embed=True),
    account_id: str | None = Body(None, embed=True),
):
    """开/关指定 worker 的自动巡逻调度。"""
    worker = _resolve_worker(account_id)
    sv = worker.supervisor
    if enable:
        sv.set_mode(AgentMode.PATROLLING)
        sv.start_scheduler()
        return {"message": f"worker={worker.account_id} 自动调度已开启"}
    sv.set_mode(AgentMode.WAITING)
    return {"message": f"worker={worker.account_id} 自动调度已关闭"}


@router.post("/agent/mode/debug")
async def set_debug_mode(account_id: str | None = Body(None, embed=True)):
    worker = _resolve_worker(account_id)
    worker.supervisor.set_mode(AgentMode.DEBUG)
    return {"message": f"worker={worker.account_id} 已进入调试模式"}


@router.post("/agent/mode/waiting")
async def set_waiting_mode(account_id: str | None = Body(None, embed=True)):
    worker = _resolve_worker(account_id)
    worker.supervisor.set_mode(AgentMode.WAITING)
    return {"message": f"worker={worker.account_id} 已进入 WAITING 模式"}



@router.get("/agent/chrome/{path:path}")
async def proxy_chrome_http(path: str, request: Request):
    async with httpx.AsyncClient() as client:
        # 将请求转发给本地 Chrome
        url = f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/{path}"
        params = dict(request.query_params)

        # 核心：必须伪造 Host 头，否则 Chrome 会报 403
        headers = {"Host": "localhost"}

        resp = await client.get(url, params=params, headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@router.websocket("/agent/chrome/ws/{page_id}")
async def proxy_chrome_ws(websocket: WebSocket, page_id: str):
    # 1. 核心修复：协商子协议，否则浏览器会主动断开
    subprotocol = websocket.headers.get("sec-websocket-protocol")
    await websocket.accept(subprotocol=subprotocol)

    chrome_ip = config.system["chrome"]["chrome_ip"]
    chrome_port = config.system["chrome"]["debug_port"]
    chrome_ws_url = f"ws://{chrome_ip}:{chrome_port}/devtools/page/{page_id}"

    try:
        # 2. 核心修复：max_size=None 允许大数据包
        async with websockets.connect(
            chrome_ws_url,
            max_size=None,
            ping_interval=None,
        ) as target_ws:

            async def forward_to_chrome():
                try:
                    # 使用更底层的 receive 循环
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.receive":
                            if "text" in message:
                                await target_ws.send(message["text"])
                            elif "bytes" in message:
                                await target_ws.send(message["bytes"])
                        elif message["type"] == "websocket.disconnect":
                            logger.info({"msg": "浏览器已主动断开连接"}, page_id)
                            break
                except Exception as e:
                    logger.debug({"msg": "Forward to Chrome stopped", "error": str(e)}, page_id)

            async def forward_to_client():
                try:
                    async for msg in target_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except Exception as e:
                    logger.debug({"msg": "Forward to Client stopped", "error": str(e)}, page_id)

            # 3. 任务管理：确保一个挂了另一个也停
            _done, pending = await asyncio.wait(
                [
                    asyncio.create_task(forward_to_chrome()),
                    asyncio.create_task(forward_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as e:
        logger.error({"msg": "WS 代理链路崩溃", "error": str(e)}, page_id)
    finally:
        # 确保彻底关闭
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/agent/debug/{page_id}")
async def get_debug_url(page_id: str, request: Request):
    # 获取当前请求的 host 和 port (即 127.0.0.1:6702)
    # 这样无论你是局域网访问还是本地访问，它都能自动适配
    host_info = request.headers.get("host")

    # 1. 构建指向你 WebSocket 代理的地址 (注意使用 ws 协议前缀的话，有些浏览器 inspector 不认，通常直接传 host:port 即可)
    custom_ws_path = f"{host_info}/api/v1/agent/chrome/ws/{page_id}"

    # 2. 构建最终 URL
    # 资源路径必须经过你的代理路径 /api/v1/agent/chrome/
    debug_url = f"http://{host_info}/api/v1/agent/chrome/devtools/inspector.html?ws={custom_ws_path}&panel=screencast"

    # 3. 直接重定向，省去手动复制
    return RedirectResponse(url=debug_url)
