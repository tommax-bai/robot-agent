from __future__ import annotations

from pydantic import BaseModel

class DTOCallbackResultRequest(BaseModel):
    trace_id: str
    result: str