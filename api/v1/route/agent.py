from fastapi import APIRouter, Body, Request, Response, WebSocket
import uuid
import asyncio
import websockets
import httpx
from agents.operator import run_task
from agents.supervisor import supervisor, AgentMode
from dto.agent import AgentRequest
import config
import utils.logger as logger
from fastapi.responses import RedirectResponse

router = APIRouter()

async def handle_agent_actions(trace_id: str, request: AgentRequest):
    """
    异步执行 ReAct Agent 任务
    """
    try:
        logger.info({
            "msg": "开始执行 Agent 任务",
            "user_goal": request.user_goal,
            "max_steps": request.max_steps
        }, trace_id)
        
        # 使用 supervisor 执行任务
        result = await supervisor.execute_task(
            user_goal=request.user_goal,
            trace_id=trace_id
        )
        
        logger.info({
            "msg": "Agent 任务完成",
            "result": result
        }, trace_id)
        
    except Exception as e:
        logger.error({
            "msg": "Agent 任务执行失败",
            "error": str(e)
        }, trace_id)


@router.post("/agent/actions")
async def router_agent_actions(request: AgentRequest = Body(...)):
    """
    启动 ReAct Agent 执行任务
    """
    trace_id = str(uuid.uuid4())
    asyncio.create_task(handle_agent_actions(trace_id, request))
    return {
        "trace_id": trace_id,
        "message": "任务已启动，正在后台执行",
        "error": None,
        "finish": False
    }


@router.post("/agent/actions/sync")
async def router_agent_actions_sync(request: AgentRequest = Body(...)):
    """
    同步执行 ReAct Agent 任务（等待完成后返回结果）
    """
    trace_id = str(uuid.uuid4())
    
    try:
        result = await supervisor.execute_task(
            user_goal=request.user_goal,
            trace_id=trace_id
        )
        
        return {
            "trace_id": trace_id,
            "message": "任务完成",
            "result": result
        }
        
    except Exception as e:
        logger.error({
            "msg": "Agent 任务执行失败",
            "error": str(e)
        }, trace_id)
        return {
            "trace_id": trace_id,
            "message": "任务失败",
            "error": str(e),
            "result": None
        }

@router.get("/agent/status")
async def get_agent_status():
    """获取当前 Agent 节点状态"""
    return supervisor.get_status()

@router.post("/agent/patrol")
async def toggle_patrol(enable: bool = Body(..., embed=True)):
    """开启或关闭自动调度"""
    if enable:
        supervisor.set_mode(AgentMode.PATROLLING)
        if not supervisor.scheduler_task or supervisor.scheduler_task.done():
            supervisor.scheduler_task = asyncio.create_task(supervisor.run_schedule_loop())
        return {"message": "自动调度已开启"}
    else:
        supervisor.set_mode(AgentMode.WAITING)
        return {"message": "自动调度已关闭"}

@router.post("/agent/mode/debug")
async def set_debug_mode():
    """切换到调试模式"""
    supervisor.set_mode(AgentMode.DEBUG)
    return {"message": "已进入调试模式，所有任务已停止"}

@router.post("/agent/mode/waiting")
async def set_debug_mode():
    """切换到调试模式"""
    supervisor.set_mode(AgentMode.WAITING)
    return {"message": "已进入WAITING模式"}

@router.post("/agent/maintenance/trigger")
async def trigger_maintenance():
    """强制触发一次养号任务"""
    trace_id = f"maint-manual-{uuid.uuid4().hex[:8]}"
    goal = "执行每日养号发帖任务：制造职场焦虑并传播知识"
    asyncio.create_task(supervisor.execute_task(user_goal=goal, trace_id=trace_id))
    return {"message": "已强制触发养号任务", "trace_id": trace_id}

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
    
    chrome_ip = config.system['chrome']['chrome_ip']
    chrome_port = config.system['chrome']['debug_port']
    chrome_ws_url = f"ws://{chrome_ip}:{chrome_port}/devtools/page/{page_id}"
    
    try:
        # 2. 核心修复：max_size=None 允许大数据包
        async with websockets.connect(
            chrome_ws_url, 
            max_size=None, 
            ping_interval=None
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
                            logger.info("浏览器已主动断开连接", page_id)
                            break # 正常退出循环
                except Exception as e:
                    logger.debug(f"Forward to Chrome stopped: {e}")

            async def forward_to_client():
                try:
                    async for msg in target_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except Exception as e:
                    logger.debug(f"Forward to Client stopped: {e}")

            # 3. 任务管理：确保一个挂了另一个也停
            done, pending = await asyncio.wait(
                [asyncio.create_task(forward_to_chrome()), 
                 asyncio.create_task(forward_to_client())],
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                
    except Exception as e:
        logger.error({"msg": "WS 代理链路崩溃", "error": str(e)}, page_id)
    finally:
        # 确保彻底关闭
        try:
            await websocket.close()
        except:
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