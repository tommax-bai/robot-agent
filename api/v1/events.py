"""SSE 事件流：dashboard 订阅 supervisor 状态变化。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from runtime import Host

router = APIRouter()


def _build_stream_handler(bus_getter):
    """生成一个 SSE handler。bus_getter() 在请求到来时才拉 bus，
    避免模块 import 时就触达 Host.current()（它要求 Host.boot 过）。"""

    async def stream(request: Request):
        bus = bus_getter()
        queue = bus.subscribe()

        async def gen():
            try:
                yield f"event: ready\ndata: {{}}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=25.0)
                        yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ka\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return stream


_handler = _build_stream_handler(lambda: Host.current().events)


@router.get("/agent/events")
async def agent_events(request: Request):
    return await _handler(request)


@router.get("/session/events")
async def session_events(request: Request):
    return await _handler(request)
