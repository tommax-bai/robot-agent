"""GoalGeneratorRegistry：按 kind 派发到对应 generator。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import GoalGenerator


class GoalGeneratorRegistry:
    def __init__(self) -> None:
        self._generators: dict[str, GoalGenerator] = {}

    def register(self, generator: GoalGenerator) -> None:
        if generator.kind in self._generators:
            raise ValueError(f"goal kind {generator.kind!r} 已注册")
        self._generators[generator.kind] = generator

    def get(self, kind: str) -> GoalGenerator:
        if kind not in self._generators:
            raise KeyError(f"未注册的 goal kind={kind!r}（已注册: {list(self._generators)}）")
        return self._generators[kind]

    def kinds(self) -> list[str]:
        return list(self._generators)
