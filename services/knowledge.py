"""
知识收割模块：从任务总结中提取标题、笔记、话题、灵感等，并持久化。

设计原则：
- 所有函数都接收 state repo，不再依赖 default_repo() 或全局函数
- harvest_knowledge 通过副作用更新 state，无返回值
- get_evolution_context 是纯查询，返回 EvolutionContext dataclass
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
    from services.agent_state import AgentStateRepo


@dataclass(frozen=True)
class EvolutionContext:
    """Agent 的进化状态与注意力权重"""
    home_weight: float
    search_weight: float
    is_mature: bool
    total_knowledge: int


def harvest_knowledge(summary: str, trace_id: str, state: AgentStateRepo) -> None:
    """
    从任务总结中收割知识，写回 state repo。
    无返回值——通过副作用更新 state。
    """
    if not summary:
        return

    snapshot = state.get()
    title_few_shots = list(snapshot["title_few_shots"])
    inspiration_pool = list(snapshot["inspiration_pool"])

    # 1. 提取爆款标题 [SHOT] 和 对应正文 [CONTENT]
    note_matches = re.findall(
        r'[\[【]SHOT[\]】][:：]?\s*(.*?)\s*[\[【]CONTENT[\]】][:：]?\s*(.*?)(?=[\[【]SHOT[\]】]|$)',
        summary, re.DOTALL
    )
    if note_matches:
        save_enabled = config.agent["maintenance"]["save_titles_to_local"]
        for title, content in note_matches:
            title = title.strip()
            content = content.strip()
            if not title:
                continue
            if title not in title_few_shots:
                title_few_shots.append(title)
            if save_enabled and content:
                _save_note_detail(title, content)
        logger.info({"msg": f"收割到 {len(note_matches)} 条笔记详情"}, trace_id)
    else:
        # 兼容旧格式
        new_shots = re.findall(r'[\[【]SHOT[\]】][:：]?\s*(.+)', summary)
        if new_shots:
            for s in new_shots:
                if s not in title_few_shots:
                    title_few_shots.append(s)
            logger.info({"msg": f"收割到 {len(new_shots)} 条爆款标题样本"}, trace_id)

    # 2. 学习笔记 [LEARNING]
    new_notes = re.findall(r'[\[【]LEARNING[\]】][:：]?\s*(.+)', summary)

    # 3. 话题词 [TAG]
    extracted_tags: list[str] = []
    for tag_line in re.findall(r'[\[【]TAG[\]】][:：]?\s*(.+)', summary):
        extracted_tags.extend(re.findall(r'#(\w+)', tag_line))

    # 4. 焦虑点 [ANXIETY]
    new_anxieties = re.findall(r'[\[【]ANXIETY[\]】][:：]?\s*(.+)', summary)

    # 5. 知识点 [KNOWLEDGE]
    new_knowledge = re.findall(r'[\[【]KNOWLEDGE[\]】][:：]?\s*(.+)', summary)

    # 6. 心情 [MOOD]
    mood_match = re.search(r'[\[【]MOOD[\]】][:：]?\s*(\w+)', summary)
    new_mood = mood_match.group(1) if mood_match else None

    # 7. 灵感 [INSIGHT] (带#话题)
    if "[INSIGHT]" in summary or "【INSIGHT】" in summary:
        for word in re.findall(r'#(\w+)', summary):
            if word not in inspiration_pool:
                inspiration_pool.append(word)

    # 8. 持久化
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


def _save_note_detail(title: str, content: str) -> None:
    try:
        os.makedirs("data/notes", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"data/notes/{timestamp}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n## 正文内容\n\n{content}\n")
        logger.info({"msg": "笔记详情已保存", "path": filename})
    except Exception as e:
        logger.error({"msg": "保存笔记详情失败", "error": str(e)})


def get_evolution_context(state: AgentStateRepo, title_few_shots: list[str]) -> EvolutionContext:
    """
    计算 Agent 的进化状态与注意力权重。
    基于 agent_state 的丰富程度决定 首页 vs 搜索 的比例。
    """
    snapshot = state.get()
    total_knowledge = (
        len(snapshot["learning_notes"])
        + len(snapshot["hashtags"])
        + len(title_few_shots)
    )

    home_weight = 0.10 if total_knowledge < 100 else 0.30
    return EvolutionContext(
        home_weight=home_weight,
        search_weight=1.0 - home_weight,
        is_mature=total_knowledge > 30,
        total_knowledge=total_knowledge,
    )

