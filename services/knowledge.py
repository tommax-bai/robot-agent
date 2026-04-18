"""services.knowledge: 从任务总结文本中提炼知识并写回 state。

职责：
- `harvest(summary, trace_id, state)` —— 从 `[SHOT]` / `[CONTENT]` / `[TAG]` /
  `[LEARNING]` / `[ANXIETY]` / `[KNOWLEDGE]` / `[MOOD]` / `[INSIGHT]` 等标签解析
  出学习成果并通过副作用写入 state（不返回）。
- `evolution_snapshot(state, title_few_shots)` —— 基于 state 丰富度计算注意力
  权重（首页 vs 搜索），供 Strategist 决策使用。纯查询。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger

if TYPE_CHECKING:
    from services.state import AgentStateRepo

_SHOT_CONTENT = re.compile(
    r"[\[【]SHOT[\]】][:：]?\s*(.*?)\s*[\[【]CONTENT[\]】][:：]?\s*(.*?)(?=[\[【]SHOT[\]】]|$)",
    re.DOTALL,
)
_SHOT_ONLY = re.compile(r"[\[【]SHOT[\]】][:：]?\s*(.+)")
_LEARNING = re.compile(r"[\[【]LEARNING[\]】][:：]?\s*(.+)")
_TAG_LINE = re.compile(r"[\[【]TAG[\]】][:：]?\s*(.+)")
_ANXIETY = re.compile(r"[\[【]ANXIETY[\]】][:：]?\s*(.+)")
_KNOWLEDGE = re.compile(r"[\[【]KNOWLEDGE[\]】][:：]?\s*(.+)")
_MOOD = re.compile(r"[\[【]MOOD[\]】][:：]?\s*(\w+)")
_INSIGHT_LINE = re.compile(r"[\[【]INSIGHT[\]】][:：]?\s*(.+)")
_HASH_WORD = re.compile(r"#(\w+)")


@dataclass(frozen=True)
class EvolutionContext:
    """Agent 进化状态与注意力权重。"""

    home_weight: float
    search_weight: float
    is_mature: bool
    total_knowledge: int


def harvest_knowledge(summary: str, trace_id: str, state: AgentStateRepo) -> None:
    """从 summary 提取知识标签，写回 state。副作用型 API，无返回。"""
    if not summary:
        return

    snapshot = state.get()
    title_few_shots = list(snapshot["title_few_shots"])
    inspiration_pool = list(snapshot["inspiration_pool"])

    # 1. [SHOT] + [CONTENT] / 兼容纯 [SHOT]
    note_pairs = _SHOT_CONTENT.findall(summary)
    if note_pairs:
        save_enabled = config.settings.agent.storage.save_titles_to_local
        for title, content in note_pairs:
            title = title.strip()
            content = content.strip()
            if not title:
                continue
            if title not in title_few_shots:
                title_few_shots.append(title)
            if save_enabled and content:
                _save_note_detail(title, content)
        logger.info({"msg": f"收割到 {len(note_pairs)} 条笔记详情"}, trace_id)
    else:
        shots = _SHOT_ONLY.findall(summary)
        for s in shots:
            if s not in title_few_shots:
                title_few_shots.append(s)
        if shots:
            logger.info({"msg": f"收割到 {len(shots)} 条爆款标题样本"}, trace_id)

    # 2. 其它标签
    new_notes = _LEARNING.findall(summary)
    extracted_tags = [w for line in _TAG_LINE.findall(summary) for w in _HASH_WORD.findall(line)]
    new_anxieties = _ANXIETY.findall(summary)
    new_knowledge = _KNOWLEDGE.findall(summary)
    mood_match = _MOOD.search(summary)
    new_mood = mood_match.group(1) if mood_match else None

    new_insights: list[str] = []
    for line in _INSIGHT_LINE.findall(summary):
        for word in _HASH_WORD.findall(line):
            if word not in inspiration_pool:
                inspiration_pool.append(word)
                new_insights.append(word)

    harvested = {
        k: v for k, v in {
            "LEARNING": len(new_notes),
            "TAG": len(extracted_tags),
            "ANXIETY": len(new_anxieties),
            "KNOWLEDGE": len(new_knowledge),
            "MOOD": new_mood or "",
            "INSIGHT": len(new_insights),
        }.items() if v
    }
    if harvested:
        logger.info({"msg": "知识收割完成", **harvested}, trace_id)
    else:
        logger.info({"msg": "知识收割完成，未提取到新标签"}, trace_id)

    state.update(
        inspiration_pool=inspiration_pool,
        last_discovery=summary,
        title_few_shots=title_few_shots,
        learning_notes=new_notes or None,
        hashtags=extracted_tags or None,
        anxiety_keywords=new_anxieties or None,
        knowledge_topics=new_knowledge or None,
        mood=new_mood,
        trace_id=trace_id,
    )


def get_evolution_context(state: AgentStateRepo, title_few_shots: list[str]) -> EvolutionContext:
    """基于 state 丰富度计算 首页 vs 搜索 的注意力权重。"""
    snapshot = state.get()
    total_knowledge = len(snapshot["learning_notes"]) + len(snapshot["hashtags"]) + len(title_few_shots)
    home_weight = 0.10 if total_knowledge < 100 else 0.30
    return EvolutionContext(
        home_weight=home_weight,
        search_weight=1.0 - home_weight,
        is_mature=total_knowledge > 30,
        total_knowledge=total_knowledge,
    )


def _save_note_detail(title: str, content: str) -> None:
    try:
        os.makedirs("data/notes", exist_ok=True)
        filename = f"data/notes/{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n## 正文内容\n\n{content}\n")
        logger.info({"msg": "笔记详情已保存", "path": filename})
    except Exception as e:
        logger.error({"msg": "保存笔记详情失败", "error": str(e)})


__all__ = ["EvolutionContext", "get_evolution_context", "harvest_knowledge"]
