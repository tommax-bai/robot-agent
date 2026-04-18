"""PlannerConfig：把 config.settings.agent.planner 封装成不可变值对象。

好处：
- 字段改名只改一处（这里 + sections.py），不用 grep 调用点
- 测试可直接构造，不依赖 config 加载
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class PlannerConfig:
    model: str
    client: str
    temperature: float
    max_tokens: int

    @classmethod
    def from_config(cls) -> PlannerConfig:
        cfg = config.settings.agent.planner
        return cls(
            model=cfg.model,
            client=cfg.llm_client,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
