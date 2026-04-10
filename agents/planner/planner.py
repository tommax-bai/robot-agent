"""
Planner Agent：任务分解。

设计原则：
- 依赖（LlmTool, SkillRegistry）通过构造注入
- 返回 Plan dataclass，不再 list[dict]
- 失败抛 PlannerError，不返回单任务兜底
- skill 过滤在 planner 内部完成，不在 operator
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import Plan, PlannerError, SubTask
from utils.prompt_template import load_prompt_template

if TYPE_CHECKING:
    from runtime.context import RunContext
    from services.skill_registry import SkillRegistry


class Planner:
    name = "planner"

    def __init__(self, skills: "SkillRegistry"):
        self._skills = skills
        cfg = config.agent["planner"]
        self._model = cfg["model"]
        self._client = cfg["llm_client"]
        self._temperature = cfg["temperature"]
        self._max_tokens = cfg["max_tokens"]

    def generate_plan(self, ctx: "RunContext", user_goal: str) -> Plan:
        """调用 LLM 生成任务规划。失败抛 PlannerError。"""
        manifests = self._available_manifests()
        manifest_text = "".join(
            f"- {m.name}: {m.description}\n" for m in manifests
        )
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        _, body = load_prompt_template("prompts/operator/plan.md")
        prompt = body.format(
            SKILL_MANIFEST=manifest_text,
            USER_GOAL=user_goal,
            CURRENT_TIME=current_time,
        )

        try:
            data, _raw = ctx.llm.call_json(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                client_name=self._client,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                trace_id=ctx.trace_id,
            )
        except Exception as e:
            logger.error({"msg": "planner LLM 调用失败", "error": str(e)}, ctx.trace_id)
            raise PlannerError(f"plan generation failed: {e}") from e

        raw_tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlannerError(f"planner 返回了无效的 tasks 字段: {data}")

        available_names = {m.name for m in manifests}
        subtasks: list[SubTask] = []
        for i, t in enumerate(raw_tasks):
            sub_goal = t.get("sub_goal") if isinstance(t, dict) else None
            if not sub_goal:
                raise PlannerError(f"子任务 {i} 缺少 sub_goal")
            skill = t.get("required_skill") if isinstance(t, dict) else None
            # planner 输出了不可用的 skill，直接降级为 None（不报错，让 operator 视觉走）
            if skill and skill not in available_names:
                logger.warning({
                    "msg": "planner 返回了不可用的 skill，已降级",
                    "skill": skill,
                }, ctx.trace_id)
                skill = None
            subtasks.append(SubTask(id=f"sub-{i}", goal=sub_goal, required_skill=skill))

        return Plan(subtasks=subtasks)

    def _available_manifests(self):
        policy = config.agent["skill_selection"]
        return self._skills.manifests_for(
            disabled_skills=set(policy["disabled_skills"]),
            disabled_groups=set(policy["disabled_skill_groups"]),
        )
