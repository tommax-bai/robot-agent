"""
Worker：单账号执行单元。

- 完整 Worker（minimal=False，默认）：持有 SessionManager + Environment +
  Dispatcher + VisionStep + RecipeOperator + Strategy + SubtaskRunner +
  Operator + Strategist + Supervisor。用于 monolith / brain 拓扑。
- 精简 Worker（minimal=True）：只持有 SessionManager + Environment +
  display_name。用于 session-only 拓扑（旧 SessionWorker 的角色）。

共享的无状态资源（LlmTool / SkillRegistry / Planner / RecipeStore 等）由 Host
构造一次并注入，多个 Worker 共用。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from runtime.events import Event, EventBus, payload
from runtime.wire import build_env, build_session, build_strategy, close_env
from tools.cloud_mobile import SessionManager
from tools.environment import Environment

if TYPE_CHECKING:
    from agents.operator import SubtaskStrategy
    from agents.planner.planner import Planner
    from services.recipes import BehaviorSummarizer, RecipeStore, TraceRecorder
    from services.skill_registry import SkillRegistry
    from services.state import AgentStateRepo
    from services.vision import PageContextCache, VisualLocator
    from tools.llm import LlmTool


class Worker:
    """单账号执行单元。"""

    def __init__(
        self,
        *,
        account_id: str,
        mode: str,
        session_mgr: SessionManager | None,
        env: Environment,
        account_cfg: dict | None = None,
        display_name: str = "",
        # 以下参数仅完整 Worker 需要；minimal Worker 可留空
        state: "AgentStateRepo | None" = None,
        llm: "LlmTool | None" = None,
        skills: "SkillRegistry | None" = None,
        page_context_cache: "PageContextCache | None" = None,
        visual_locator: "VisualLocator | None" = None,
        recipe_store: "RecipeStore | None" = None,
        trace_recorder: "TraceRecorder | None" = None,
        behavior_summarizer: "BehaviorSummarizer | None" = None,
        events: EventBus | None = None,
        planner: "Planner | None" = None,
        minimal: bool = False,
    ):
        self.account_id = account_id
        self.mode = mode
        self.account_cfg = account_cfg or {}
        self.display_name = display_name or account_id
        self.session_mgr = session_mgr
        self.env = env
        self.minimal = minimal

        if minimal:
            # session-only 拓扑：不装配决策链
            self.state = None
            self.supervisor = None
            self.strategist = None
            self.operator = None
            self.subtask_runner = None
            self.strategy = None
            self.vision_step = None
            self.recipe_operator = None
            self.dispatcher = None
            self._events = events
            self._swap_lock = asyncio.Lock()
            return

        # 完整 Worker 装配
        assert state is not None and llm is not None and skills is not None
        assert page_context_cache is not None and visual_locator is not None
        assert recipe_store is not None and trace_recorder is not None
        assert behavior_summarizer is not None and events is not None and planner is not None

        self.state = state
        self._llm = llm
        self._skills = skills
        self._page_context_cache = page_context_cache
        self._visual_locator = visual_locator
        self._recipe_store = recipe_store
        self._trace_recorder = trace_recorder
        self._behavior_summarizer = behavior_summarizer
        self._events = events
        self._planner = planner

        from agents.operator import (
            ActionDispatcher,
            RecipeOperator,
            VisionConfig,
            VisionPromptBuilder,
            build_default_subtask_runner,
            build_default_vision_action_step,
        )
        from agents.operator.operator import Operator
        from agents.strategist import build_default_strategist
        from agents.supervisor.supervisor import Supervisor

        self.strategist = build_default_strategist(llm)
        self.dispatcher = ActionDispatcher(skills=skills, env=env)

        self._vision_cfg = VisionConfig.from_config()
        self._vision_prompt_builder = VisionPromptBuilder(skills=skills)
        self._vision_prompt_builder.preload()

        self.vision_step = build_default_vision_action_step(
            dispatcher=self.dispatcher,
            env=env,
            skills=skills,
            recorder=trace_recorder,
            page_cache=page_context_cache,
            mode=mode,
            cfg=self._vision_cfg,
            prompt_builder=self._vision_prompt_builder,
        )
        self.recipe_operator = RecipeOperator(
            dispatcher=self.dispatcher,
            recipes=recipe_store,
            page_cache=page_context_cache,
            locator=visual_locator,
            env=env,
            recorder=trace_recorder,
        )
        self.strategy = build_strategy(
            mode, vision_step=self.vision_step,
            session_mgr=session_mgr, account_cfg=account_cfg,
        )
        self.subtask_runner = build_default_subtask_runner(
            strategy=self.strategy,
            recipe_operator=self.recipe_operator,
            behavior_summarizer=behavior_summarizer,
        )
        self.operator = Operator(planner=planner, runner=self.subtask_runner)
        self.supervisor = Supervisor(
            operator=self.operator,
            strategist=self.strategist,
            state=state,
            llm=llm,
            event_bus=events,
            account_id=account_id,
        )
        self._swap_lock = asyncio.Lock()

    # ─── 热切换 ────────────────────────────────────────────────

    async def swap_runtime_mode(self, new_mode: str) -> dict:
        if self.minimal:
            raise RuntimeError("minimal worker 不支持 swap_runtime_mode")

        valid = {"local_chrome", "cloudmobile", "agentbay"}
        if new_mode not in valid:
            raise ValueError(f"未知 mode={new_mode}，可选: {sorted(valid)}")

        async with self._swap_lock:
            old_mode = self.mode
            if new_mode == old_mode:
                return {"ok": True, "message": f"已经处于 {new_mode}，未作切换", "mode": new_mode}

            if self.supervisor.is_busy():
                snap = self.supervisor.status()
                raise RuntimeError(
                    f"worker={self.account_id} 当前任务 trace={snap.current_trace_id} 正在运行，"
                    "请先等其结束或调 /api/v1/agent/cancel 取消"
                )

            import utils.logger as log

            log.info({"msg": "runtime.mode hot-swap 开始", "account": self.account_id,
                      "old": old_mode, "new": new_mode})

            if self.session_mgr is not None:
                try:
                    self.session_mgr.release()
                except Exception as e:
                    log.warning({"msg": "旧 session 释放异常（继续切换）", "error": str(e)})

            from agents.operator import build_default_vision_action_step

            try:
                new_session = build_session(new_mode, self.account_cfg, self._events, self.account_id)
                new_env = build_env(new_mode, self.account_cfg, session_mgr=new_session)
                new_vision = build_default_vision_action_step(
                    dispatcher=self.dispatcher,
                    env=new_env,
                    skills=self._skills,
                    recorder=self._trace_recorder,
                    page_cache=self._page_context_cache,
                    mode=new_mode,
                    cfg=self._vision_cfg,
                    prompt_builder=self._vision_prompt_builder,
                )
                new_strategy = build_strategy(
                    new_mode, vision_step=new_vision,
                    session_mgr=new_session, account_cfg=self.account_cfg,
                )
            except Exception as e:
                log.error({"msg": "新 env 构造失败", "target_mode": new_mode, "error": str(e)})
                raise

            old_env = self.env
            self.mode = new_mode
            self.session_mgr = new_session
            self.env = new_env
            self.dispatcher.set_env(new_env)
            self.vision_step = new_vision
            self.recipe_operator.set_env(new_env)
            self.strategy = new_strategy
            self.subtask_runner.set_strategy(new_strategy)

            close_env(old_env)

            self._events.publish(
                Event.RUNTIME_CHANGED,
                payload(
                    account=self.account_id, old_mode=old_mode, new_mode=new_mode,
                    env=type(new_env).__name__,
                    strategy=type(new_strategy).__name__,
                ),
            )
            log.info({"msg": "runtime.mode hot-swap 完成", "account": self.account_id,
                      "mode": new_mode, "env": type(new_env).__name__,
                      "strategy": type(new_strategy).__name__})
            return {
                "ok": True, "account": self.account_id, "mode": new_mode,
                "env": type(new_env).__name__,
                "strategy": type(new_strategy).__name__,
            }

    # ─── 生命周期 ─────────────────────────────────────────────

    def shutdown(self) -> None:
        if self.session_mgr is not None:
            try:
                self.session_mgr.release()
            except Exception:
                pass
        close_env(self.env)

    def release_session(self, force: bool = False) -> dict:
        """主动释放当前 session（停计费、登录态丢失）。"""
        if self.session_mgr is None:
            return {
                "ok": True,
                "account": self.account_id,
                "message": f"当前 mode={self.mode} 无云手机 session，noop",
            }

        if not self.minimal and self.supervisor.is_busy():
            if not force:
                snap = self.supervisor.status()
                raise RuntimeError(
                    f"worker={self.account_id} 当前任务 trace={snap.current_trace_id} 正在运行，"
                    "请先等其结束或调 /api/v1/agent/cancel 取消"
                )
            try:
                self.supervisor.request_stop("force_release")
            except Exception:
                pass

        import utils.logger as log

        log.info({"msg": "主动释放 session", "account": self.account_id})
        try:
            self.session_mgr.release()
        except Exception as e:
            log.warning({"msg": "release_session 异常（继续）", "error": str(e)})
        return {
            "ok": True,
            "account": self.account_id,
            "message": "session 已释放，下次发任务会创建新 session 并需要重新扫码登录",
        }


__all__ = ["Worker"]
