from __future__ import annotations

from pydantic import BaseModel


class AgentRequest(BaseModel):
    """Agent 请求参数"""

    user_goal: str
    # 多账号场景下指定执行 worker；省略则用 WorkerPool.default()
    account_id: str | None = None
