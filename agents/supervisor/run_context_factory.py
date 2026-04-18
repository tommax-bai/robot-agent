"""
RunContextFactory: 为每次 Supervisor 任务派发构造独立的 RunContext。

把 Supervisor.make_ctx + uuid 分配拎出来做成工厂，方便：
- Job 拿 factory 而不是 Supervisor（去除对 Supervisor 内部状态的依赖）
- trace_id 前缀统一（patrol- / post- / dm- / cr- / …）
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from runtime.ctx import CancelScope, RunContext

if TYPE_CHECKING:
    from services.state import AgentStateRepo
    from tools.llm import LlmTool


class RunContextFactory:
    def __init__(self, state: AgentStateRepo, llm: LlmTool):
        self._state = state
        self._llm = llm

    def create(self, trace_id: str) -> RunContext:
        """为一次任务构造独立 RunContext（独立的 cancel token）。"""
        return RunContext(
            trace_id=trace_id,
            cancel=CancelScope(),
            state=self._state,
            llm=self._llm,
        )

    @staticmethod
    def new_trace_id(prefix: str | None = None) -> str:
        """生成新 trace_id；带 prefix 用 hex[:8] 短格式，不带 prefix 用完整 uuid4。"""
        short = uuid.uuid4().hex[:8]
        return f"{prefix}-{short}" if prefix else str(uuid.uuid4())
