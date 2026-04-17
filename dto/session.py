"""Session Service 的请求/响应 DTO。"""

from __future__ import annotations

from pydantic import BaseModel


class SessionAcquireRequest(BaseModel):
    account_id: str = "default"
    trace_id: str = ""


class SessionReleaseRequest(BaseModel):
    account_id: str = "default"
    force: bool = False


class ScreenshotRequest(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    include_cursor: bool = True


class ActionRequest(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    action: dict


class DelegateTaskRequest(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    goal: str
    max_steps: int = 50
    timeout_seconds: int = 300


class WorkerCreateRequest(BaseModel):
    account_id: str | None = None
    image_id: str | None = None
    count: int = 1
