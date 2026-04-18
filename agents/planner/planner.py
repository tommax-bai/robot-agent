"""Planner：把用户目标拆成 SubTask 列表。

Thin Facade 组合 4 个协作者：
- PlanPromptBuilder：模板 + 变量注入
- LLM 调用（通过 ctx.llm）
- PlanParser：顶层 JSON 结构校验
- SubTaskNormalizer：逐项 skill/intent 归一化

与旧实现的关键差异：
- async：LLM 调用用 asyncio.to_thread 包，不再阻塞 asyncio loop
- 模板缓存：PlanPromptBuilder 只加载一次
- 配置集中：PlannerConfig 值对象取代 cfg["..."] 散读
- 职责分离：prompt / parse / normalize 各一个类，可单独测
- 失败抛 PlannerError，不返回单任务兜底
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agents.base import Plan, PlannerError
from agents.planner.config import PlannerConfig
from agents.planner.normalizer import SubTaskNormalizer
from agents.planner.parser import PlanParser
from agents.planner.prompt import PlanPromptBuilder

if TYPE_CHECKING:
    from runtime.ctx import RunContext
    from services.intents import IntentRegistry
    from services.skill_registry import SkillRegistry


class Planner:
    name = "planner"

    def __init__(
        self,
        cfg: PlannerConfig,
        prompt_builder: PlanPromptBuilder,
        parser: PlanParser,
        normalizer: SubTaskNormalizer,
    ):
        self._cfg = cfg
        self._prompt_builder = prompt_builder
        self._parser = parser
        self._normalizer = normalizer

    async def generate_plan(self, ctx: RunContext, user_goal: str) -> Plan:
        """调用 LLM 生成任务规划。失败抛 PlannerError。"""
        prompt = self._prompt_builder.build(user_goal)

        try:
            data, _raw = await asyncio.to_thread(
                ctx.llm.call_json,
                messages=[{"role": "user", "content": prompt}],
                model=self._cfg.model,
                client_name=self._cfg.client,
                temperature=self._cfg.temperature,
                max_tokens=self._cfg.max_tokens,
                trace_id=ctx.trace_id,
            )
        except Exception as e:
            # `plan.failed` 最终由 operator 在捕获到 PlannerError 时发布；
            # 这里不再重复日志，交由 llm_failed 追踪底层原因。
            raise PlannerError(f"plan generation failed: {e}") from e

        raw_tasks = self._parser.parse(data, _raw)
        return Plan(subtasks=tuple(self._normalizer.normalize(raw_tasks, trace_id=ctx.trace_id)))


def build_default_planner(skills: SkillRegistry, intents: IntentRegistry) -> Planner:
    """默认 wiring：预加载模板，组装 4 个协作者。"""
    prompt_builder = PlanPromptBuilder(skills=skills, intents=intents)
    prompt_builder.preload()
    return Planner(
        cfg=PlannerConfig.from_config(),
        prompt_builder=prompt_builder,
        parser=PlanParser(),
        normalizer=SubTaskNormalizer(skills=skills, intents=intents),
    )
