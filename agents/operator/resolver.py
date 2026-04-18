"""SubtaskResolver：统一 recipe 快路径和 strategy 主路径。

把原 SubtaskRunner 里的 if/else 硬分支换成可遍历的 Resolver 链。
每个 Resolver 试完后返回 ResolverOutcome，告诉 Runner 自己是否解决了子任务：
- resolved：完成了，result 字段是 TaskResult
- skipped：我这层不适用（没命中 / 未配置等），请让下一层 Resolver 接管
- failed：我这层尝试了但失败了（比如续航也耗尽），result 是 TaskResult.failure

Runner 遇到 resolved 或 failed 就停；遇到 skipped 就继续下一个 Resolver。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from agents.base import SubTask, TaskResult
    from runtime.ctx import RunContext


ResolverKind = Literal["resolved", "skipped", "failed"]


@dataclass(frozen=True)
class ResolverOutcome:
    kind: ResolverKind
    result: TaskResult | None = None
    reason: str = ""

    @classmethod
    def resolved(cls, result: TaskResult) -> ResolverOutcome:
        return cls(kind="resolved", result=result)

    @classmethod
    def skipped(cls, reason: str = "") -> ResolverOutcome:
        return cls(kind="skipped", reason=reason)

    @classmethod
    def failed(cls, result: TaskResult, reason: str = "") -> ResolverOutcome:
        return cls(kind="failed", result=result, reason=reason)


class SubtaskResolver(Protocol):
    name: str

    async def resolve(self, subtask: SubTask, ctx: RunContext) -> ResolverOutcome: ...
