"""
IntentRegistry: Planner 和 BehaviorSummarizer 的共享 intent 词表。

设计原则：
- 从 data/intent_registry.json 加载，是 intent 的唯一真相源
- 提供格式化方法，供 prompt 注入
- 支持模糊匹配，将 LLM 可能输出的变体归一化到标准 intent
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import utils.logger as logger


@dataclass(frozen=True)
class IntentEntry:
    """单个 intent 的元数据"""

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    skill: str | None = None


class IntentRegistry:
    """加载并查询 intent 注册表"""

    def __init__(self, path: str | Path = "data/intent_registry.json"):
        self._path = Path(path)
        self._entries: dict[str, IntentEntry] = {}
        self._alias_index: dict[str, str] = {}  # alias -> intent name
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning({"msg": "intent 注册表不存在", "path": str(self._path)})
            return
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning({"msg": "intent 注册表读取失败", "error": str(e)})
            return

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.warning({"msg": "intent 注册表 JSON 解析失败，使用空注册表", "error": str(e), "path": str(self._path)})
            return

        if not isinstance(data, dict):
            logger.warning({"msg": "intent 注册表顶层不是 dict，使用空注册表", "path": str(self._path)})
            return

        intents = data.get("intents")
        if not isinstance(intents, dict):
            logger.warning({"msg": "intent 注册表缺少 intents 字段或格式错误，使用空注册表", "path": str(self._path)})
            return

        skipped = 0
        for name, raw in intents.items():
            if not isinstance(raw, dict):
                skipped += 1
                continue
            # 必须有 description 字段
            if "description" not in raw:
                logger.warning({"msg": "intent 条目缺少 description，已跳过", "intent": name})
                skipped += 1
                continue
            aliases_raw = raw.get("aliases", [])
            if not isinstance(aliases_raw, list):
                aliases_raw = []
            aliases = tuple(str(a) for a in aliases_raw)
            entry = IntentEntry(
                name=name,
                description=str(raw["description"]),
                aliases=aliases,
                skill=raw.get("skill"),
            )
            self._entries[name] = entry
            # 建立 alias → intent name 的反向索引
            for alias in aliases:
                self._alias_index[alias] = name
            # intent name 本身也作为别名
            self._alias_index[name] = name

        if skipped:
            logger.warning({"msg": f"intent 注册表加载完成，跳过 {skipped} 个无效条目", "loaded": len(self._entries)})

    def resolve(self, raw_intent: str) -> str:
        """将 LLM 输出的 intent 归一化到注册表中的标准名称。
        找不到时原样返回（视为新 intent）。"""
        if not raw_intent:
            return "unknown"
        # 精确匹配
        if raw_intent in self._entries:
            return raw_intent
        # 别名匹配
        if raw_intent in self._alias_index:
            return self._alias_index[raw_intent]
        # 模糊匹配：遍历别名看是否被包含
        for alias, intent_name in self._alias_index.items():
            if alias in raw_intent or raw_intent in alias:
                return intent_name
        return raw_intent

    def get(self, name: str) -> IntentEntry | None:
        return self._entries.get(name)

    def list_all(self) -> list[IntentEntry]:
        return list(self._entries.values())

    def format_for_prompt(self) -> str:
        """生成供 prompt 注入的 intent 候选列表文本"""
        lines: list[str] = []
        for entry in self._entries.values():
            aliases_str = "、".join(entry.aliases[:4]) if entry.aliases else ""
            line = f"- {entry.name}: {entry.description}"
            if aliases_str:
                line += f"（别名: {aliases_str}）"
            lines.append(line)
        return "\n".join(lines)

    def known_intent_names(self) -> set[str]:
        return set(self._entries.keys())
