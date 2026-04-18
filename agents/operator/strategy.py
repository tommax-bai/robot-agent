"""
SubtaskStrategy: 子任务执行策略协议。

把"如何把 SubTask 变成 TaskResult"从 SubtaskRunner 里抽出来，让 Runner 只
负责通用的 recipe 快路径 + 续航重试，决策实现可在不同 mode 间替换：

- VisionActionStep        — 自家 VLM 驱动的 observe→think→act 视觉循环（local_chrome / cloudmobile）
- AgentBayDelegateStrategy — AgentBay 内置 mobile_use Agent 接管整个子任务（agentbay）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agents.base import SubTask, TaskResult
    from runtime.ctx import RunContext


class SubtaskStrategy(Protocol):
    name: str

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult: ...
