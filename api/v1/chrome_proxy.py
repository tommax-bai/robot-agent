"""本地 Chrome DevTools HTTP/WS 代理 + DevTools Inspector 跳转。

只在本地 chrome 模式下有意义；routes 按资源剥离，不再混在 agent 路由里。
"""
from __future__ import annotations

import asyncio

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import RedirectResponse

import config
import utils.logger as logger

router = APIRouter()


@router.get("/agent/chrome/{path:path}")
async def proxy_http(path: str, request: Request):
    chrome = config.settings.system.chrome
    async with httpx.AsyncClient() as client:
        url = f"http://{chrome.chrome_ip}:{chrome.debug_port}/{path}"
        # 核心：伪造 Host 头，否则 Chrome 会 403
        resp = await client.get(url, params=dict(request.query_params), headers={"Host": "localhost"})
        return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))


@router.websocket("/agent/chrome/ws/{page_id}")
async def proxy_ws(websocket: WebSocket, page_id: str):
    # 协商子协议，否则浏览器会主动断开
    subprotocol = websocket.headers.get("sec-websocket-protocol")
    await websocket.accept(subprotocol=subprotocol)

    chrome = config.settings.system.chrome
    target = f"ws://{chrome.chrome_ip}:{chrome.debug_port}/devtools/page/{page_id}"

    try:
        async with websockets.connect(target, max_size=None, ping_interval=None) as up:
            async def client_to_chrome():
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.receive":
                            if "text" in message:
                                await up.send(message["text"])
                            elif "bytes" in message:
                                await up.send(message["bytes"])
                        elif message["type"] == "websocket.disconnect":
                            logger.info({"msg": "浏览器已主动断开连接"}, page_id)
                            break
                except Exception as e:
                    logger.debug({"msg": "Forward to Chrome stopped", "error": str(e)}, page_id)

            async def chrome_to_client():
                try:
                    async for msg in up:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except Exception as e:
                    logger.debug({"msg": "Forward to Client stopped", "error": str(e)}, page_id)

            _done, pending = await asyncio.wait(
                [
                    asyncio.create_task(client_to_chrome()),
                    asyncio.create_task(chrome_to_client()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except Exception as e:
        logger.error({"msg": "WS 代理链路崩溃", "error": str(e)}, page_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/agent/debug/{page_id}")
async def devtools_redirect(page_id: str, request: Request):
    """拼一个指向 DevTools Inspector 的 URL，通过本进程的 WS 代理走。"""
    host_info = request.headers.get("host")
    custom_ws = f"{host_info}/api/v1/agent/chrome/ws/{page_id}"
    debug_url = f"http://{host_info}/api/v1/agent/chrome/devtools/inspector.html?ws={custom_ws}&panel=screencast"
    return RedirectResponse(url=debug_url)
