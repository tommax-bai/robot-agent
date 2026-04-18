"""Selector：把随机性从 Strategist 内部抽成可注入依赖。

生产用 RandomSelector；测试可注入 StubSelector 做 deterministic 断言。
"""

from __future__ import annotations

import random
from typing import Protocol, Sequence


class Selector(Protocol):
    def choose(self, items: Sequence[str]) -> str: ...
    def roll(self) -> float: ...


class RandomSelector:
    def choose(self, items: Sequence[str]) -> str:
        return random.choice(items)

    def roll(self) -> float:
        return random.random()


class StubSelector:
    """测试用：固定返回第一个元素 / 固定 roll 值。"""

    def __init__(self, roll_value: float = 0.0):
        self._roll = roll_value

    def choose(self, items: Sequence[str]) -> str:
        return items[0]

    def roll(self) -> float:
        return self._roll
