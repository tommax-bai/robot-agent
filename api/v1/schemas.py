"""API 请求/响应 DTO 统一存放处（替代旧 `dto/` 目录）。

命名约定：
- `*Req`  — 请求体
- `*Resp` — 显式响应体（多数端点直接返回 dict，不定义响应模型）
"""
from __future__ import annotations

from pydantic import BaseModel


# ── /tasks ────────────────────────────────────────────────────

class TaskSubmitReq(BaseModel):
    """通用任务提交。user_goal 是老字段名，保持兼容。"""
    user_goal: str
    account_id: str | None = None


# ── /callbacks ────────────────────────────────────────────────

class CallbackResultReq(BaseModel):
    trace_id: str
    result: str


# ── /sessions ─────────────────────────────────────────────────

class SessionAcquireReq(BaseModel):
    account_id: str = "default"
    trace_id: str = ""


class SessionReleaseReq(BaseModel):
    account_id: str = "default"
    force: bool = False


class ScreenshotReq(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    include_cursor: bool = True


class ActionReq(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    action: dict


class DelegateTaskReq(BaseModel):
    account_id: str = "default"
    trace_id: str = ""
    goal: str
    max_steps: int = 50
    timeout_seconds: int = 300


class DelegateCancelReq(BaseModel):
    account_id: str = "default"
    trace_id: str


class WorkerCreateReq(BaseModel):
    account_id: str | None = None
    image_id: str | None = None


__all__ = [
    "ActionReq",
    "CallbackResultReq",
    "DelegateCancelReq",
    "DelegateTaskReq",
    "ScreenshotReq",
    "SessionAcquireReq",
    "SessionReleaseReq",
    "TaskSubmitReq",
    "WorkerCreateReq",
]
