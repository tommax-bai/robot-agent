"""services.intents: Planner 与 RecipeMiner 共享的 intent 词表。

设计：
- 单一真相源 `data/intent_registry.json`。
- `resolve()` 把 LLM 输出（可能是别名、同义词、模糊词）归一化到标准 intent 名。
- `format_for_prompt()` 产出给 LLM 的候选清单文本，供 plan / miner 双向注入。
- 初始化时若 JSON 缺失 / 损坏，降级为空表并发 warning —— 不抛。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import utils.logger as logger


@dataclass(frozen=True)
class Intent:
    """单个 intent 的元数据。"""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    skill: str | None = None


class IntentRegistry:
    """加载并查询 intent 注册表。"""

    def __init__(self, path: str | Path = "data/intent_registry.json"):
        self._path = Path(path)
        self._entries: dict[str, Intent] = {}
        self._alias_index: dict[str, str] = {}  # alias → intent name
        self._load()

    # ─── 查询 ─────────────────────────────────────────────────

    def resolve(self, raw_intent: str) -> str:
        """把 LLM 原始 intent 归一化到注册表里的标准名；找不到返回原值。"""
        if not raw_intent:
            return "unknown"
        if raw_intent in self._entries:
            return raw_intent
        if raw_intent in self._alias_index:
            return self._alias_index[raw_intent]
        for alias, intent_name in self._alias_index.items():
            if alias in raw_intent or raw_intent in alias:
                return intent_name
        return raw_intent

    def get(self, name: str) -> Intent | None:
        return self._entries.get(name)

    def list_all(self) -> list[Intent]:
        return list(self._entries.values())

    def known_intent_names(self) -> set[str]:
        return set(self._entries.keys())

    def format_for_prompt(self) -> str:
        """产出供 prompt 注入的候选清单文本。"""
        lines: list[str] = []
        for entry in self._entries.values():
            aliases = "、".join(entry.aliases[:4])
            line = f"- {entry.name}: {entry.description}"
            if aliases:
                line += f"（别名: {aliases}）"
            lines.append(line)
        return "\n".join(lines)

    # ─── 加载 ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning({"msg": "intent 注册表不存在", "path": str(self._path)})
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning({"msg": "intent 注册表读取失败", "error": str(e), "path": str(self._path)})
            return
        if not isinstance(data, dict):
            logger.warning({"msg": "intent 注册表顶层不是 dict", "path": str(self._path)})
            return

        intents = data.get("intents")
        if not isinstance(intents, dict):
            logger.warning({"msg": "intent 注册表缺少 intents 字段", "path": str(self._path)})
            return

        skipped = 0
        for name, raw in intents.items():
            intent = _parse_entry(name, raw)
            if intent is None:
                skipped += 1
                continue
            self._entries[name] = intent
            for alias in intent.aliases:
                self._alias_index[alias] = name
            self._alias_index[name] = name

        if skipped:
            logger.warning({"msg": f"intent 注册表加载完成，跳过 {skipped} 条无效", "loaded": len(self._entries)})


def _parse_entry(name: str, raw: object) -> Intent | None:
    if not isinstance(raw, dict) or "description" not in raw:
        return None
    aliases_raw = raw.get("aliases", [])
    aliases = tuple(str(a) for a in aliases_raw) if isinstance(aliases_raw, list) else ()
    return Intent(
        name=name,
        description=str(raw["description"]),
        aliases=aliases,
        skill=raw.get("skill"),
    )


__all__ = ["Intent", "IntentRegistry"]
