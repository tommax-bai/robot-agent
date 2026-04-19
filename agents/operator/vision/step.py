"""VisionActionStep: 单个子任务的视觉-动作循环（slim 版）。

三件事的协作：
- VisionPromptBuilder    — 组 system prompt（模板缓存）
- 本类                   — observe → think → act 主循环
- StepFeedback           — 上一步反馈 / 动作格式化

与旧实现的关键差异：
- 模板预加载缓存，不再每次子任务 reload 3 次磁盘
- VisionConfig 值对象替代 cfg["..."] dict 散读
- 5 个 format/hash static 方法搬去 StepFeedback
- history_rounds 从 config 读，不再硬编码 6
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from agents.base import (
    DecisionParseError,
    StepBudgetExceededError,
    SubTask,
    TaskResult,
)
from utils.events import Ev, ev
from agents.operator.vision.decision_parser import DecisionParser
from agents.operator.vision.history import ConversationHistory
from agents.operator.vision.step_feedback import StepFeedback
from agents.operator.vision.types import Decision, Observation

if TYPE_CHECKING:
    from agents.operator.action_dispatcher import ActionDispatcher
    from runtime.ctx import RunContext
    from services.recipes import TraceRecorder
    from services.skill_registry import SkillRegistry
    from services.vision import PageContextCache
    from tools.environment import Environment

from agents.operator.vision.config import VisionConfig
from agents.operator.vision.prompt_builder import VisionPromptBuilder


class VisionActionStep:
    name = "vision_action"

    def __init__(
        self,
        cfg: VisionConfig,
        prompt_builder: VisionPromptBuilder,
        dispatcher: ActionDispatcher,
        env: Environment,
        recorder: TraceRecorder | None = None,
        page_cache: PageContextCache | None = None,
        mode: str = "local_chrome",
    ):
        self._cfg = cfg
        self._prompt_builder = prompt_builder
        self._dispatcher = dispatcher
        self._env = env
        self._recorder = recorder
        self._page_cache = page_cache
        self._mode = mode

    def set_env(self, env: Environment, mode: str | None = None) -> None:
        self._env = env
        if mode is not None:
            self._mode = mode

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        history = ConversationHistory(max_rounds=self._cfg.history_rounds)
        history.set_system(self._prompt_builder.build_system_prompt(subtask))

        last_decision: Decision | None = None
        last_outcome = None
        last_observation_hash = ""

        for step in range(self._cfg.max_steps):
            ctx.cancel.raise_if_cancelled()

            observation = await self._observe(ctx)
            observation_hash = StepFeedback.hash_observation(observation)
            page_context = (
                self._page_cache.get_or_classify(ctx.trace_id, observation)
                if self._page_cache is not None
                else None
            )
            if page_context is not None and page_context.page_state != "unknown":
                ev(Ev.VISION_OBSERVED, mode=self._mode, step=step,
                   page_state=page_context.page_state,
                   confidence=round(page_context.confidence, 2))
            feedback = StepFeedback.build_feedback(
                last_decision=last_decision,
                last_outcome=last_outcome,
                last_observation_hash=last_observation_hash,
                current_observation_hash=observation_hash,
            )
            decision = await self._think(history, observation, last_outcome, feedback, step, ctx)
            ctx.cancel.raise_if_cancelled()

            outcome = await self._dispatcher.dispatch(decision.actions, ctx)
            self._log_outcome(step, outcome)
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

        raise StepBudgetExceededError(
            f"子任务 '{subtask.goal}' 达到最大步数 {self._cfg.max_steps}"
        )

    async def _observe(self, ctx: RunContext) -> Observation:
        # env.capture 对 cloud 是 HTTP 阻塞调用（~200-500ms），
        # 丢线程池避免挂死 asyncio loop。
        image_b64, _, _ = await asyncio.to_thread(self._env.capture, ctx.trace_id, True)
        return Observation(
            image_base64=image_b64,
            captured_at=datetime.now().isoformat(),
            content_type=getattr(self._env, "content_type", "image/jpeg"),
        )

    async def _think(
        self,
        history: ConversationHistory,
        observation: Observation,
        last_outcome,
        feedback: str,
        step: int,
        ctx: RunContext,
    ) -> Decision:
        last_summary = StepFeedback.format_last_outcome(last_outcome)
        user_text = (
            f"【当前步数: {step}/{self._cfg.max_steps}】\n"
            f"上一步执行结果: {last_summary}\n"
            f"{feedback}"
            f"请根据当前image输出下一步动作 JSON。"
        )
        image_url = f"data:{observation.content_type};base64,{observation.image_base64}"
        history.add_user(
            [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        )

        try:
            parsed, raw = await asyncio.to_thread(
                ctx.llm.call_json,
                messages=history.to_messages(),
                model=self._cfg.model,
                client_name=self._cfg.client,
                temperature=self._cfg.temperature,
                trace_id=ctx.trace_id,
            )
        except Exception:
            # caller 已发 llm.failed；这里直接冒泡
            raise

        history.add_assistant(raw)

        decision = DecisionParser.parse(parsed, raw)  # DecisionParseError 由 subtask_runner 捕获

        ev(Ev.VISION_DECIDED, mode=self._mode, step=step,
           action_count=len(decision.actions),
           actions=[StepFeedback.format_action_brief(a) for a in decision.actions],
           thought=decision.thought)
        return decision

    def _log_outcome(self, step: int, outcome) -> None:
        if outcome.is_finish:
            ev(Ev.VISION_FINISHED, mode=self._mode, step=step, summary=outcome.summary)


def build_default_vision_action_step(
    *,
    dispatcher: ActionDispatcher,
    env: Environment,
    skills: SkillRegistry,
    recorder: TraceRecorder | None = None,
    page_cache: PageContextCache | None = None,
    mode: str = "local_chrome",
    cfg: VisionConfig | None = None,
    prompt_builder: VisionPromptBuilder | None = None,
) -> VisionActionStep:
    """默认 wiring：VisionConfig + 预加载的 PromptBuilder + VisionActionStep。

    调用方可选传入已构造的 cfg / prompt_builder 以便 hot-swap 场景复用。
    """
    if cfg is None:
        cfg = VisionConfig.from_config()
    if prompt_builder is None:
        prompt_builder = VisionPromptBuilder(skills=skills)
        prompt_builder.preload()
    return VisionActionStep(
        cfg=cfg,
        prompt_builder=prompt_builder,
        dispatcher=dispatcher,
        env=env,
        recorder=recorder,
        page_cache=page_cache,
        mode=mode,
    )
