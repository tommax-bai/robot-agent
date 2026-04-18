"""Persona：从 config 一次性加载的人设身份值对象。

替代原来散落在 Strategist 里的 5 个 `_persona_xxx` getter。
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class Persona:
    name: str
    character: str
    style: str
    strategy: str
    recruitment_info: str

    @classmethod
    def from_config(cls) -> Persona:
        p = config.settings.agent.persona
        return cls(
            name=p.name,
            character=p.character,
            style=p.style,
            strategy=p.strategy,
            recruitment_info=p.recruitment_info,
        )
