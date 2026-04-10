from pydantic import BaseModel


class AgentRequest(BaseModel):
    """Agent 请求参数"""
    user_goal: str
