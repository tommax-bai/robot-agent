"""services.skill_registry: 对 agents/runtime 暴露的技能注册表。

本模块是 `skills.SkillRegistry` 的服务层薄包装 —— 真正的 pack 发现 /
装饰器 / 调用分发都在 `skills/_lib/` 里。这里保留旧导入路径，避免一次
性改动所有 caller（agents.planner.*、agents.operator.*、runtime.host）。
"""

from __future__ import annotations

from skills import PackSpec, SkillRegistry, ToolContext, ToolOutcome

# 旧名字兼容：历史上这里叫 SkillManifest，现在 PackSpec 是统一的 spec 类型。
SkillManifest = PackSpec

__all__ = [
    "PackSpec",
    "SkillManifest",
    "SkillRegistry",
    "ToolContext",
    "ToolOutcome",
]
