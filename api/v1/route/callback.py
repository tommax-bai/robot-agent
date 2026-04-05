from fastapi import APIRouter, Body
import uuid
import asyncio
from dto.agent import AgentRequest
import utils.logger as logger
from dto.callback import DTOCallbackResultRequest
import utils.waiting_queue as waiting_queue

router = APIRouter()



@router.post("/callback/result")
async def router_callback_result(request: DTOCallbackResultRequest):
    response = waiting_queue.update_queue(request.trace_id, request.result)
    return response