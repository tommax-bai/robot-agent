"""Vision-Action 循环数据结构（re-export 自 services.contracts）。

实际定义在 `services.contracts` —— services 层是更底层的依赖，避免
services → agents 反向依赖。本模块保留以兼容旧导入路径。
"""

from __future__ import annotations

from services.contracts import (
    Action,
    ActionOutcome,
    ActionResult,
    Decision,
    Observation,
)

__all__ = [
    "Action",
    "ActionOutcome",
    "ActionResult",
    "Decision",
    "Observation",
]
