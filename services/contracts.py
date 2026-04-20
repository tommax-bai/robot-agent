"""services.contracts: Vision-Action 循环共享数据类型。

原先这些类型放在 `agents.base`，导致 `services.vision` / `services.recipes`
需要反向导入 agents。Clean-slate 将其下沉到 services 层，agents 依然能用
（`agents.base` 以 re-export 形式对外），但不再出现 services→agents 循环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Observation:
    """单次屏幕观察快照。"""

    image_base64: str
    captured_at: str  # ISO 时间字符串
    content_type: str = "image/jpeg"  # MIME；WuyingMobileEnv 可能走 image/png


@dataclass(frozen=True)
class Action:
    """单个原子动作。"""

    method: str
    params: dict[str, Any]
    delay: float | None = None


@dataclass(frozen=True)
class Decision:
    """LLM 一次决策的解析结果。"""

    thought: str
    actions: list[Action]
    raw: str  # 原始文本，供日志/排错


@dataclass(frozen=True)
class ActionResult:
    """单个动作执行后的结果。"""

    method: str
    ok: bool
    message: str = ""
    payload: Any = None


@dataclass(frozen=True)
class ActionOutcome:
    """一拍动作执行的最终结果。"""

    results: list[ActionResult]
    is_finish: bool = False
    summary: str = ""

    @classmethod
    def finished(cls, summary: str, results: list[ActionResult]) -> ActionOutcome:
        return cls(results=results, is_finish=True, summary=summary)

    @classmethod
    def continuing(cls, results: list[ActionResult]) -> ActionOutcome:
        return cls(results=results, is_finish=False)


__all__ = ["Action", "ActionOutcome", "ActionResult", "Decision", "Observation"]
