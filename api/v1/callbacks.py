"""外部回调（扫码登录的验证码回填等）。"""
from __future__ import annotations

from fastapi import APIRouter

from api.v1.schemas import CallbackResultReq
from services.waiting_queue import default as waiting_queue

router = APIRouter()


@router.post("/callback/result")
async def callback_result(req: CallbackResultReq):
    return waiting_queue.complete(req.trace_id, req.result)
