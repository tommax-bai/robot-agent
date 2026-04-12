from __future__ import annotations

from fastapi import APIRouter

import utils.waiting_queue as waiting_queue
from dto.callback import DTOCallbackResultRequest

router = APIRouter()


@router.post("/callback/result")
async def router_callback_result(request: DTOCallbackResultRequest):
    response = waiting_queue.update_queue(request.trace_id, request.result)
    return response
