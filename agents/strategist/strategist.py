"""
Strategist: 内容策略服务。

当前能力：
- brainstorm: LLM 生成话题列表
- generate_patrol_goal: 生成下一轮巡逻任务的目标指令
- generate_posting_goal: 生成下一轮发帖任务的目标指令

设计原则：
- 依赖（state, llm）通过构造函数注入；不接收 RunContext
- _render_and_call 统一模板加载 → 变量注入 → LLM 调用流水线
- 副作用（写 state）由调用方决定，不藏在 brainstorm 内部
- 失败抛 StrategistError，绝不返回业务话术兜底
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import StrategistError
from services.knowledge import EvolutionContext
from utils.prompt_template import load_prompt_template

if TYPE_CHECKING:
    from services.agent_state import AgentStateRepo
    from tools.llm_tool import LlmTool


@dataclass(frozen=True)
class BrainstormResult:
    topics: list[str]


class Strategist:
    name = "strategist"

    def __init__(
        self,
        state: AgentStateRepo,
        llm: LlmTool,
        prompts_dir: str = "prompts/agent",
    ):
        self._state = state
        self._llm = llm
        self._prompts_dir = prompts_dir

    # ── 公开能力 ──────────────────────────────────────────────

    async def brainstorm(
        self,
        inspiration_pool: list[str],
        trace_id: str = "system",
    ) -> BrainstormResult:
        """灵感发散：返回话题列表，不写 state（副作用由调用方决定）"""
        snapshot = self._state.get()
        text = self._render_and_call(
            template="brainstorm.md",
            trace_id=trace_id,
            persona_name=self._persona_name(),
            core_strategy=self._core_strategy(),
            time_context=self._time_context(),
            inspiration_keywords=", ".join(inspiration_pool[:10]) or "（暂无，请自由发散）",
            anxiety_keywords=", ".join(snapshot["anxiety_keywords"][:8]) or "（暂无）",
            knowledge_keywords=", ".join(snapshot["knowledge_topics"][:8]) or "（暂无）",
            forbidden_words=", ".join(snapshot["recent_searches"][-10:]) or "（无）",
            default_temperature=0.9,
        )
        topics = [t.strip() for t in text.replace("，", ",").split(",") if t.strip()]
        if not topics:
            raise StrategistError("brainstorm returned empty topic list")
        return BrainstormResult(topics=topics)

    async def generate_patrol_goal(
        self,
        inspiration_pool: list[str],
        evolution: EvolutionContext,
        trace_id: str = "system",
    ) -> str:
        """生成巡逻目标。失败抛 StrategistError。"""
        brainstorm = await self.brainstorm(inspiration_pool, trace_id=trace_id)
        # 副作用上移：brainstorm 完成后写一次 recent_searches
        self._state.update(recent_searches=[brainstorm.topics[0]], trace_id=trace_id)

        snapshot = self._state.get()
        is_home_focus = random.random() < evolution.home_weight

        return self._render_and_call(
            template="patrol_goal.md",
            trace_id=trace_id,
            persona_name=self._persona_name(),
            persona_character=self._persona_character(),
            current_time_str=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_knowledge=evolution.total_knowledge,
            home_weight_pct=f"{evolution.home_weight*100:.0f}",
            search_weight_pct=f"{evolution.search_weight*100:.0f}",
            focus_desc="【首页沉浸】" if is_home_focus else "【主动探索】",
            search_keyword=random.choice(brainstorm.topics),
            mood=snapshot["mood"],
            default_temperature=0.3,
        )

    async def generate_posting_goal(
        self,
        inspiration_pool: list[str],
        trace_id: str = "system",
    ) -> str:
        """
        生成发帖任务目标。
        失败抛 StrategistError。
        """
        brainstorm = await self.brainstorm(inspiration_pool, trace_id=trace_id)
        topic = random.choice(brainstorm.topics)

        snapshot = self._state.get()
        title_few_shots = snapshot["title_few_shots"][-10:]
        title_few_shots_str = (
            "\n".join(f"- {t}" for t in title_few_shots)
            if title_few_shots else "（暂无样本，请自由发挥）"
        )

        return self._render_and_call(
            template="posting_goal.md",
            trace_id=trace_id,
            persona_name=self._persona_name(),
            persona_character=self._persona_character(),
            persona_style=self._persona_style(),
            core_strategy=self._core_strategy(),
            topic=topic,
            recruitment_info=config.agent["maintenance"]["recruitment_info"],
            title_few_shots=title_few_shots_str,
            last_discovery=snapshot["last_discovery"] or "（暂无近期收割内容）",
            default_temperature=0.8,
        )

    # ── 内部辅助 ──────────────────────────────────────────────

    def _render_and_call(
        self,
        *,
        template: str,
        trace_id: str,
        default_temperature: float,
        **vars,
    ) -> str:
        """统一的 模板加载 → 变量注入 → LLM 调用 流水线"""
        try:
            template_path = os.path.join(self._prompts_dir, template)
            meta, body = load_prompt_template(template_path)
            prompt = body.format(**vars)
            return self._llm.call_with_template(meta, prompt, default_temperature=default_temperature)
        except StrategistError:
            raise
        except Exception as e:
            logger.error(
                {"msg": "strategist 模板调用失败", "template": template, "error": str(e)},
                trace_id,
            )
            raise StrategistError(f"render_and_call({template}) failed: {e}") from e

    def _persona_name(self) -> str:
        return config.agent["maintenance"]["persona"]["name"]

    def _persona_character(self) -> str:
        return config.agent["maintenance"]["persona"]["character"]

    def _persona_style(self) -> str:
        return config.agent["maintenance"]["persona"]["style"]

    def _core_strategy(self) -> str:
        return config.agent["maintenance"]["core_strategy"]

    def _time_context(self) -> str:
        now = datetime.now()
        return f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}, 星期{now.isoweekday()}"
