"""Skill 体系公共类型。

设计要点：
- `ToolContext`：唯一上下文载体，显式注入 trace_id；取代旧注册表的签名反射魔法。
- `ToolOutcome`：所有工具返回的统一结构，取代未定型 dict。
- `ToolSpec` / `PackSpec`：loader 生成的只读元数据，供 registry / prompt / planner 读取。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolContext:
    """每次工具调用的上下文。扩展字段时显式声明，不走签名反射。"""

    trace_id: str


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行结果。所有字段在 `to_dict()` 里原样输出给上层（ActionDispatcher / LLM）。"""

    ok: bool
    message: str = ""
    finish: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str = "", *, finish: bool = False, **data: Any) -> "ToolOutcome":
        return cls(ok=True, message=message, finish=finish, data=data)

    @classmethod
    def failure(cls, message: str, **data: Any) -> "ToolOutcome":
        return cls(ok=False, message=message, data=data)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "message": self.message, "finish": self.finish}
        out.update(self.data)
        return out


# ─── 只读规格（loader 产出，registry 消费）────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    pack: str           # 所属 pack 的 name，例如 "rednote.auth"
    name: str           # 工具短名（pack 内唯一），例如 "open_rednote_homepage"
    description: str
    fn: Callable[..., ToolOutcome]
    # None = 继承 pack.supports；给定 = 精确限制本工具可用的 runtime 模式
    # 用法：pack 整体可用于 cloudmobile，但其中某个 selenium-only 工具 supports=("local_chrome",)
    supports: tuple[str, ...] | None = None

    @property
    def fqname(self) -> str:
        return f"{self.pack}.{self.name}"


@dataclass(frozen=True)
class PackSpec:
    """对外暴露的 pack 元数据快照（不含可变状态）。"""

    name: str
    description: str
    supports: tuple[str, ...]
    instructions: str
    tools: tuple[ToolSpec, ...]
