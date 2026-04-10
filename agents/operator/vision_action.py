"""
VisionActionStep: 单个子任务的视觉-动作循环。

4 阶段拆分：
  1. _build_system_prompt — 构造 system prompt（一次性）
  2. _observe              — 截屏
  3. _think                — LLM 决策（同时写入 history）
  4. _act                  — 通过 ActionDispatcher 执行

核心改进：
- ConversationHistory 是注入的对象，不再依赖全局可变 dict
- 每一拍只在两个明确点检查 cancel，由异常统一处理
- LLM 输出脏数据清洗集中在 Decision.parse()，本类不再做参数 hack
- 失败抛结构化异常 (StepBudgetExceededError, DecisionParseError, CancelledError)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import config
import utils.logger as logger
from agents.base import (
    Action,
    Decision,
    DecisionParseError,
    Observation,
    StepBudgetExceededError,
    SubTask,
    TaskResult,
)
from runtime.context import ConversationHistory
from utils.prompt_template import load_prompt_template

if TYPE_CHECKING:
    from agents.operator.action_dispatcher import ActionDispatcher
    from runtime.context import RunContext
    from services.skill_registry import SkillRegistry
    import tools.screenshot as screenshot_module


class VisionActionStep:
    name = "vision_action"

    def __init__(
        self,
        skills: "SkillRegistry",
        dispatcher: "ActionDispatcher",
        screenshot_fn=None,
    ):
        self._skills = skills
        self._dispatcher = dispatcher
        # 截屏函数可注入用于测试；默认使用 tools.screenshot
        if screenshot_fn is None:
            import tools.screenshot as _screenshot
            screenshot_fn = _screenshot.get_screenshot_base64
        self._screenshot_fn = screenshot_fn
        cfg = config.agent["operator"]
        self._model = cfg["model"]
        self._client = cfg["llm_client"]
        self._temperature = cfg["temperature"]
        self._max_steps = cfg.get("max_steps", 40)

    async def run(self, subtask: SubTask, ctx: "RunContext") -> TaskResult:
        """
        执行单个子任务的视觉-动作循环。
        成功返回 TaskResult.success(summary)，
        步数耗尽抛 StepBudgetExceededError，
        被取消抛 CancelledError。
        """
        history = ConversationHistory(max_rounds=6)
        history.set_system(self._build_system_prompt(subtask))

        last_outcome = None

        for step in range(self._max_steps):
            ctx.cancel.raise_if_cancelled()

            observation = self._observe(ctx)
            decision = self._think(history, observation, last_outcome, step, ctx)
            ctx.cancel.raise_if_cancelled()  # LLM 返回后再检查一次

            outcome = await self._dispatcher.dispatch(decision.actions, ctx)
            last_outcome = outcome

            if outcome.is_finish:
                return TaskResult.success(summary=outcome.summary)

        raise StepBudgetExceededError(
            f"子任务 '{subtask.goal}' 达到最大步数 {self._max_steps}"
        )

    # ── 4 阶段方法 ────────────────────────────────────────────

    def _build_system_prompt(self, subtask: SubTask) -> str:
        """组装 system prompt：动作规范 + 技能通用规则 + 具体技能 + 子目标约束"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        _, action_body = load_prompt_template("prompts/operator/action.md")
        action_section = action_body.format(CURRENT_TIME=current_time)

        _, common_rules_body = load_prompt_template("prompts/operator/common_skill_rules.md")

        skill_section = ""
        if subtask.required_skill:
            skill_section = self._skills.build_prompt([subtask.required_skill])

        _, constraint_body = load_prompt_template("prompts/operator/sub_goal_constraint.md")
        constraint_section = constraint_body.format(sub_goal=subtask.goal)

        return "\n".join([action_section, common_rules_body, skill_section, constraint_section])

    def _observe(self, ctx: "RunContext") -> Observation:
        image_b64, _, _ = self._screenshot_fn(ctx.trace_id, include_cursor=True)
        return Observation(image_base64=image_b64, captured_at=datetime.now().isoformat())

    def _think(
        self,
        history: ConversationHistory,
        observation: Observation,
        last_outcome,
        step: int,
        ctx: "RunContext",
    ) -> Decision:
        last_summary = self._format_last_outcome(last_outcome)
        user_text = (
            f"【当前步数: {step}/{self._max_steps}】\n"
            f"上一步执行结果: {last_summary}\n"
            f"请根据当前截图输出下一步动作 JSON。"
        )
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{observation.image_base64}"}},
        ]
        history.add_user(user_content)

        try:
            parsed, raw = ctx.llm.call_json(
                messages=history.to_messages(),
                model=self._model,
                client_name=self._client,
                temperature=self._temperature,
                trace_id=ctx.trace_id,
            )
        except Exception as e:
            logger.error({"msg": "LLM 调用失败", "step": step, "error": str(e)}, ctx.trace_id)
            raise

        history.add_assistant(raw)
        logger.info({"msg": f"Step {step} LLM Raw", "raw": raw}, ctx.trace_id)

        try:
            decision = Decision.parse(parsed, raw)
        except DecisionParseError as e:
            logger.error({"msg": "Decision 解析失败", "error": str(e)}, ctx.trace_id)
            raise

        logger.info(
            {
                "msg": f"Step {step} 决策",
                "thought": decision.thought,
                "action_count": len(decision.actions),
            },
            ctx.trace_id,
        )
        return decision

    @staticmethod
    def _format_last_outcome(outcome) -> str:
        if outcome is None:
            return json.dumps({"ok": True, "message": "任务开始"}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": all(r.ok for r in outcome.results),
                "results": [
                    {"method": r.method, "ok": r.ok, "message": r.message}
                    for r in outcome.results
                ],
            },
            ensure_ascii=False,
        )
