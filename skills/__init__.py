"""Skills 包根：对外暴露声明 API（Pack / ToolContext / ToolOutcome / SkillRegistry）。

Pack 作者在 `skills/<pack_dir>/__init__.py` 里 `from skills import Pack, ...`。
服务层在 `services/skill_registry.py` 里 `from skills import SkillRegistry`。
"""

from __future__ import annotations

from skills._lib import Pack, PackSpec, SkillRegistry, ToolContext, ToolOutcome, ToolSpec

__all__ = [
    "Pack",
    "PackSpec",
    "SkillRegistry",
    "ToolContext",
    "ToolOutcome",
    "ToolSpec",
]
