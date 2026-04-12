from __future__ import annotations

import asyncio
import uuid

import httpx
import websockets
from fastapi import APIRouter, Body, Request, Response, WebSocket
from fastapi.responses import RedirectResponse

import config
import utils.logger as logger
from agents.base import Task
from agents.supervisor import AgentMode
from dto.agent import AgentRequest
from runtime.container import get_container

router = APIRouter()


def _supervisor():
    return get_container().supervisor


async def handle_agent_actions(trace_id: str, request: AgentRequest):
    """异步执行 Agent 任务"""
    try:
        logger.info(
            {
                "msg": "开始执行 Agent 任务",
                "user_goal": request.user_goal,
            },
            trace_id,
        )

        task = Task(kind="user_goal", goal=request.user_goal)
        result = await _supervisor().execute_task(task, trace_id=trace_id)

        logger.info({"msg": "Agent 任务完成", "ok": result.ok}, trace_id)
    except Exception as e:
        logger.error({"msg": "Agent 任务执行失败", "error": str(e)}, trace_id)


@router.post("/agent/actions")
async def router_agent_actions(request: AgentRequest = Body(...)):
    """启动 Agent 执行任务（异步）"""
    trace_id = str(uuid.uuid4())
    asyncio.create_task(handle_agent_actions(trace_id, request))
    return {
        "trace_id": trace_id,
        "message": "任务已启动，正在后台执行",
        "result": None,
        "error": None,
    }


@router.post("/agent/actions/sync")
async def router_agent_actions_sync(request: AgentRequest = Body(...)):
    """同步执行 Agent 任务"""
    trace_id = str(uuid.uuid4())
    try:
        task = Task(kind="user_goal", goal=request.user_goal)
        result = await _supervisor().execute_task(task, trace_id=trace_id)
        return {
            "trace_id": trace_id,
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


@router.get("/agent/status")
async def get_agent_status():
    return _supervisor().get_status()


@router.post("/agent/patrol")
async def toggle_patrol(enable: bool = Body(..., embed=True)):
    sv = _supervisor()
    if enable:
        sv.set_mode(AgentMode.PATROLLING)
        sv.start_scheduler()
        return {"message": "自动调度已开启"}
    sv.set_mode(AgentMode.WAITING)
    return {"message": "自动调度已关闭"}


@router.post("/agent/mode/debug")
async def set_debug_mode():
    _supervisor().set_mode(AgentMode.DEBUG)
    return {"message": "已进入调试模式，所有任务已停止"}


@router.post("/agent/mode/waiting")
async def set_waiting_mode():
    _supervisor().set_mode(AgentMode.WAITING)
    return {"message": "已进入WAITING模式"}



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
