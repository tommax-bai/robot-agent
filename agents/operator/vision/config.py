"""VisionConfig：把 config.settings.agent.operator 封装成不可变值对象。

与 PlannerConfig 同构。额外携带 vision loop 专属的 max_steps / history_rounds。
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class VisionConfig:
    model: str
    client: str
    temperature: float
    max_steps: int
    history_rounds: int

    @classmethod
    def from_config(cls) -> VisionConfig:
        cfg = config.settings.agent.operator
        return cls(
            model=cfg.model,
            client=cfg.llm_client,
            temperature=cfg.temperature,
            max_steps=cfg.max_steps,
            history_rounds=cfg.history_rounds,
        )
