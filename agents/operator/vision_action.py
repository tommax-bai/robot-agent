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

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
    from services.action_memory import TraceRecorder
    from services.skill_registry import SkillRegistry
    from services.vision import PageContextCache
    from tools.backends import ActionBackend


class VisionActionStep:
    name = "vision_action"

    def __init__(
        self,
        skills: SkillRegistry,
        dispatcher: ActionDispatcher,
        backend: ActionBackend,
        recorder: TraceRecorder | None = None,
        page_cache: PageContextCache | None = None,
    ):
        self._skills = skills
        self._dispatcher = dispatcher
        self._backend = backend
        self._recorder = recorder
        self._page_cache = page_cache
        cfg = config.agent["operator"]
        self._model = cfg["model"]
        self._client = cfg["llm_client"]
        self._temperature = cfg["temperature"]
        self._max_steps = cfg.get("max_steps", 40)

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        """
        执行单个子任务的视觉-动作循环。
        成功返回 TaskResult.success(summary)，
        步数耗尽抛 StepBudgetExceededError，
        被取消抛 CancelledError。
        """
        history = ConversationHistory(max_rounds=6)
        history.set_system(self._build_system_prompt(subtask))

        last_decision: Decision | None = None
        last_outcome = None
        last_observation_hash = ""

        for step in range(self._max_steps):
            ctx.cancel.raise_if_cancelled()

            observation = self._observe(ctx)
            observation_hash = self._hash_observation(observation)
            page_context = (
                self._page_cache.get_or_classify(ctx.trace_id, observation) if self._page_cache is not None else None
            )
            if page_context is not None and page_context.page_state != "unknown":
                logger.info(
                    {
                        "msg": f"Step {step} 页面识别",
                        "page_state": page_context.page_state,
                        "confidence": f"{page_context.confidence:.2f}",
                    },
                    ctx.trace_id,
                )
            feedback = self._build_step_feedback(
                last_decision=last_decision,
                last_outcome=last_outcome,
                last_observation_hash=last_observation_hash,
                current_observation_hash=observation_hash,
            )
            decision = self._think(history, observation, last_outcome, feedback, step, ctx)
            ctx.cancel.raise_if_cancelled()  # LLM 返回后再检查一次

            outcome = await self._dispatcher.dispatch(decision.actions, ctx)
            outcome_parts = []
            for r in outcome.results:
                status = "✓" if r.ok else "✗"
                part = f"{status} {r.method}"
                if not r.ok and r.message:
                    part += f"({r.message[:60]})"
                outcome_parts.append(part)
            outcome_desc = ", ".join(outcome_parts)
            if outcome.is_finish:
                logger.info(
                    {"msg": f"Step {step} 任务完成", "summary": outcome.summary},
                    ctx.trace_id,
                )
            else:
                logger.info(
                    {"msg": f"Step {step} 执行结果", "outcome": outcome_desc},
                    ctx.trace_id,
                )
            if self._recorder is not None:
                self._recorder.record_llm_step(
                    trace_id=ctx.trace_id,
                    subtask=subtask,
                    step=step,
                    observation=observation,
                    decision=decision,
                    outcome=outcome,
                    page_context=page_context,
                )
            last_decision = decision
            last_outcome = outcome
            last_observation_hash = observation_hash

            if outcome.is_finish:
                return TaskResult.success(summary=outcome.summary)

        raise StepBudgetExceededError(f"子任务 '{subtask.goal}' 达到最大步数 {self._max_steps}")

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

    def _observe(self, ctx: RunContext) -> Observation:
        image_b64, _, _ = self._backend.screenshot(ctx.trace_id, include_cursor=True)
        return Observation(image_base64=image_b64, captured_at=datetime.now().isoformat())

    def _think(
        self,
        history: ConversationHistory,
        observation: Observation,
        last_outcome,
        feedback: str,
        step: int,
        ctx: RunContext,
    ) -> Decision:
        last_summary = self._format_last_outcome(last_outcome)
        user_text = (
            f"【当前步数: {step}/{self._max_steps}】\n"
            f"上一步执行结果: {last_summary}\n"
            f"{feedback}"
            f"请根据当前image输出下一步动作 JSON。"
        )
        image_url = f"data:image/jpeg;base64,{observation.image_base64}"
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        history.add_user(user_content)
        logger.info({"msg": "观察到新画面，正在决策下一步动作", "step": step, "text": user_text}, ctx.trace_id)

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

        try:
            decision = Decision.parse(parsed, raw)
        except DecisionParseError as e:
            logger.error({"msg": "Decision 解析失败", "error": str(e)}, ctx.trace_id)
            raise

        actions_desc = " → ".join(self._format_action_brief(a) for a in decision.actions)
        logger.info(
            {
                "msg": f"Step {step} 决策",
                "thought": decision.thought,
                "actions": actions_desc,
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
                "results": [{"method": r.method, "ok": r.ok, "message": r.message} for r in outcome.results],
            },
            ensure_ascii=False,
        )

    @classmethod
    def _build_step_feedback(
        cls,
        *,
        last_decision: Decision | None,
        last_outcome,
        last_observation_hash: str,
        current_observation_hash: str,
    ) -> str:
        if last_decision is None or last_outcome is None:
            return ""

        lines = [f"上一步动作: {cls._format_last_actions(last_decision)}"]
        if last_observation_hash and current_observation_hash == last_observation_hash:
            lines.append(
                "页面变化判断: 上一步动作执行后截图没有变化。"
                "这通常说明点击位置不对、目标元素不可点击，或页面没有响应。"
                "不要重复同一坐标；请重新根据当前截图定位，必要时换搜索入口或使用工具。"
            )
        elif last_observation_hash:
            lines.append("页面变化判断: 上一步动作执行后截图发生变化，请基于当前截图重新判断。")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_last_actions(decision: Decision) -> str:
        actions: list[dict[str, Any]] = []
        for action in decision.actions:
            params = action.params
            actions.append(
                {
                    "method": action.method,
                    "x": params.get("x"),
                    "y": params.get("y"),
                    "description": params.get("description"),
                }
            )
        return json.dumps(actions, ensure_ascii=False)

    @staticmethod
    def _format_action_brief(action: Action) -> str:
        p = action.params
        desc = p.get("description", "")
        if action.method in ("click", "dblclick", "move"):
            return f"{action.method}({p.get('x')},{p.get('y')} {desc})"
        if action.method == "scroll":
            return f"scroll({p.get('clicks')} {desc})"
        if action.method == "paste":
            text = p.get("text", "")
            preview = text[:20] + "…" if len(text) > 20 else text
            return f"paste(\"{preview}\")"
        if action.method == "hotkey":
            return f"hotkey({p.get('keys')})"
        if action.method == "finish":
            return f"finish({p.get('summary', '')[:40]})"
        if action.method == "wait":
            return f"wait({p.get('milliseconds')}ms)"
        if action.method == "drag":
            return f"drag({p.get('x1')},{p.get('y1')}→{p.get('x2')},{p.get('y2')} {desc})"
        return f"{action.method}({desc})"

    @staticmethod
    def _hash_observation(observation: Observation) -> str:
        return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]
