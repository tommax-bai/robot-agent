from pydantic import BaseModel


class AgentRequest(BaseModel):
    """Agent 请求参数"""
    user_goal: str
    max_steps: int = 100
    checker_delay: float = 0.3
