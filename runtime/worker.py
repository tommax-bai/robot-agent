"""
Worker：单账号执行单元。

每个 Worker 持有一份**完全独立**的执行链路：
- 自己的 SessionManager（per-worker 云手机 session 生命周期）
- 自己的 backend（动作执行）
- 自己的 dispatcher / vision_step / recipe_operator / strategy / subtask_runner / operator / supervisor

共享的资源（LLM client、skills 词表、page registry、recipe 库、event bus、planner 等）由
AppContainer 持有并注入。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import config
from agents.operator import (
    ActionDispatcher,
    AgentBayDelegateStrategy,
    Operator,
    RecipeOperator,
    SubtaskRunner,
    SubtaskStrategy,
    VisionActionStep,
)
from agents.strategist import Strategist
from agents.supervisor.supervisor import Supervisor
from tools.backends import ActionBackend, AgentBayBackend, MacOSChromeBackend, SessionManager

if TYPE_CHECKING:
    from agents.planner.planner import Planner
    from runtime.event_bus import EventBus
    from services.action_memory import BehaviorSummarizer, RecipeStore, TraceRecorder
    from services.agent_state import AgentStateRepo
    from services.skill_registry import SkillRegistry
    from services.vision import PageContextCache, VisualLocator
    from tools.llm_tool import LlmTool


class Worker:
    """单账号的执行单元。"""

    def __init__(
        self,
        *,
        account_id: str,
        mode: str,
        state: AgentStateRepo,
        session_mgr: SessionManager | None,
        backend: ActionBackend,
        # 共享依赖
        llm: LlmTool,
        skills: SkillRegistry,
        page_context_cache: PageContextCache,
        visual_locator: VisualLocator,
        recipe_store: RecipeStore,
        trace_recorder: TraceRecorder,
        behavior_summarizer: BehaviorSummarizer,
        event_bus: EventBus,
        planner: Planner,
        account_cfg: dict | None = None,
    ):
        self.account_id = account_id
        self.mode = mode
        self.account_cfg = account_cfg or {}
        self.state = state
        self.session_mgr = session_mgr
        self.backend = backend

        self._shared = {
            "llm": llm,
            "skills": skills,
            "page_context_cache": page_context_cache,
            "visual_locator": visual_locator,
            "recipe_store": recipe_store,
            "trace_recorder": trace_recorder,
            "behavior_summarizer": behavior_summarizer,
            "event_bus": event_bus,
            "planner": planner,
        }

        self.strategist = Strategist(state=state, llm=llm)
        self.dispatcher = ActionDispatcher(skills=skills, backend=backend)
        self.vision_step = VisionActionStep(
            skills=skills,
            dispatcher=self.dispatcher,
            backend=backend,
            recorder=trace_recorder,
            page_cache=page_context_cache,
            mode=mode,
        )
        self.recipe_operator = RecipeOperator(
            dispatcher=self.dispatcher,
            recipes=recipe_store,
            page_cache=page_context_cache,
            locator=visual_locator,
            backend=backend,
            recorder=trace_recorder,
        )
        self.strategy: SubtaskStrategy = build_strategy(
            mode, backend=backend, vision_step=self.vision_step,
            session_mgr=session_mgr, account_cfg=account_cfg,
        )
        self.subtask_runner = SubtaskRunner(
            strategy=self.strategy,
            recipe_operator=self.recipe_operator,
            behavior_summarizer=behavior_summarizer,
            max_resumes=2,
        )
        self.operator = Operator(planner=planner, runner=self.subtask_runner)
        self.supervisor = Supervisor(
            operator=self.operator,
            strategist=self.strategist,
            state=state,
            llm=llm,
            event_bus=event_bus,
            account_id=account_id,
        )

        self._swap_lock = asyncio.Lock()

    async def swap_runtime_mode(self, new_mode: str) -> dict:
        """热切换本 worker 的 runtime.mode。"""
        valid = {"local_chrome", "cloudmobile", "agentbay"}
        if new_mode not in valid:
            raise ValueError(f"未知 mode={new_mode}，可选: {sorted(valid)}")

        async with self._swap_lock:
            old_mode = self.mode
            if new_mode == old_mode:
                return {"ok": True, "message": f"已经处于 {new_mode}，未作切换", "mode": new_mode}

            if self.supervisor.has_active_task():
                run = self.supervisor.current_run
                raise RuntimeError(
                    f"worker={self.account_id} 当前任务 trace={run.trace_id} 正在运行，"
                    "请先等其结束或调 /api/v1/agent/cancel 取消"
                )

            import utils.logger as _logger

            _logger.info(
                {"msg": "runtime.mode hot-swap 开始", "account": self.account_id, "old": old_mode, "new": new_mode}
            )

            if self.session_mgr is not None:
                try:
                    self.session_mgr.release()
                except Exception as e:
                    _logger.warning({"msg": "旧 session 释放异常（继续切换）", "error": str(e)})

            try:
                new_session_mgr = build_session_mgr(
                    new_mode, self.account_cfg, self._shared["event_bus"], self.account_id,
                )
                new_backend = build_backend(new_mode, self.account_cfg, session_mgr=new_session_mgr)
                new_vision = VisionActionStep(
                    skills=self._shared["skills"],
                    dispatcher=self.dispatcher,
                    backend=new_backend,
                    recorder=self._shared["trace_recorder"],
                    page_cache=self._shared["page_context_cache"],
                    mode=new_mode,
                )
                new_strategy = build_strategy(
                    new_mode, backend=new_backend, vision_step=new_vision,
                    session_mgr=new_session_mgr, account_cfg=self.account_cfg,
                )
            except Exception as e:
                _logger.error({"msg": "新 backend 构造失败", "target_mode": new_mode, "error": str(e)})
                raise

            self.mode = new_mode
            self.session_mgr = new_session_mgr
            self.backend = new_backend
            self.dispatcher.set_backend(new_backend)
            self.vision_step._backend = new_backend
            self.recipe_operator.set_backend(new_backend)
            self.strategy = new_strategy
            self.subtask_runner.set_strategy(new_strategy)

            from runtime.event_bus import EVT_RUNTIME_CHANGED, make_event_payload

            self._shared["event_bus"].publish(
                EVT_RUNTIME_CHANGED,
                make_event_payload(
                    account=self.account_id,
                    old_mode=old_mode,
                    new_mode=new_mode,
                    backend=type(new_backend).__name__,
                    strategy=type(new_strategy).__name__,
                ),
            )
            _logger.info(
                {
                    "msg": "runtime.mode hot-swap 完成",
                    "account": self.account_id,
                    "mode": new_mode,
                    "backend": type(new_backend).__name__,
                    "strategy": type(new_strategy).__name__,
                }
            )
            return {
                "ok": True,
                "account": self.account_id,
                "mode": new_mode,
                "backend": type(new_backend).__name__,
                "strategy": type(new_strategy).__name__,
            }

    def shutdown(self) -> None:
        if self.session_mgr is not None:
            try:
                self.session_mgr.release()
            except Exception:
                pass

    def release_session(self, force: bool = False) -> dict:
        """主动释放当前 session（停计费、登录态丢失）。"""
        if self.session_mgr is None:
            return {
                "ok": True,
                "account": self.account_id,
                "message": f"当前 mode={self.mode} 无云手机 session，noop",
            }

        if self.supervisor.has_active_task():
            if not force:
                run = self.supervisor.current_run
                raise RuntimeError(
                    f"worker={self.account_id} 当前任务 trace={run.trace_id} 正在运行，"
                    "请先等其结束或调 /api/v1/agent/cancel 取消"
                )
            try:
                self.supervisor.request_cancel("force_release")
            except Exception:
                pass

        import utils.logger as _logger

        _logger.info({"msg": "主动释放 session", "account": self.account_id})
        try:
            self.session_mgr.release()
        except Exception as e:
            _logger.warning({"msg": "release_session 异常（继续）", "error": str(e)})
        return {
            "ok": True,
            "account": self.account_id,
            "message": "session 已释放，下次发任务会创建新 session 并需要重新扫码登录",
        }


# ─── 工厂函数 ────────────────────────────────────────────────

def _is_remote_mode() -> bool:
    return bool(config.agent["runtime"].get("session_service_url"))


def build_session_mgr(
    mode: str,
    account_cfg: dict | None,
    event_bus: EventBus,
    account_id: str,
) -> SessionManager | None:
    """云端模式构造 SessionManager。remote 模式或 local_chrome 返回 None。"""
    if mode == "local_chrome" or _is_remote_mode():
        return None
    ab = config.agent["runtime"]["agentbay"]
    cfg = account_cfg or {}
    return SessionManager(
        api_key=ab["api_key"],
        image_id=cfg.get("image_id") or ab["image_id"],
        idle_release_timeout=ab.get("idle_release_timeout"),
        mode=mode,
        event_bus=event_bus,
        account_id=account_id,
    )


def build_backend(
    mode: str,
    account_cfg: dict | None = None,
    session_mgr: SessionManager | None = None,
) -> ActionBackend:
    rt = config.agent["runtime"]
    session_url = rt.get("session_service_url", "")

    # 远程模式：截图/动作通过 HTTP 调 Session Service
    if session_url and mode in ("cloudmobile", "agentbay"):
        from tools.backends.remote import RemoteBackend
        account_id = (account_cfg or {}).get("id") or "default"
        return RemoteBackend(
            session_service_url=session_url,
            account_id=account_id,
            api_key=rt.get("session_service_api_key", ""),
            mode=mode,
        )

    match mode:
        case "local_chrome":
            return MacOSChromeBackend()
        case "cloudmobile" | "agentbay":
            if session_mgr is None:
                raise RuntimeError("云端模式必须提供 SessionManager")
            ab = rt["agentbay"]
            cfg = account_cfg or {}
            return AgentBayBackend(
                session_mgr=session_mgr,
                screenshot_format=cfg.get("screenshot_format") or ab["screenshot_format"],
                mode=mode,
            )
        case _:
            raise RuntimeError(f"未知 mode={mode!r}，可选值: local_chrome / cloudmobile / agentbay")


def build_strategy(
    mode: str,
    *,
    backend: ActionBackend,
    vision_step: VisionActionStep,
    session_mgr: SessionManager | None = None,
    account_cfg: dict | None = None,
) -> SubtaskStrategy:
    rt = config.agent["runtime"]
    session_url = rt.get("session_service_url", "")

    match mode:
        case "local_chrome" | "cloudmobile":
            return vision_step
        case "agentbay":
            if session_url:
                from tools.backends.remote_delegate import RemoteDelegateStrategy
                account_id = (account_cfg or {}).get("id") or "default"
                return RemoteDelegateStrategy(
                    session_service_url=session_url,
                    account_id=account_id,
                    api_key=rt.get("session_service_api_key", ""),
                )
            if session_mgr is None:
                raise RuntimeError("agentbay 模式必须提供 SessionManager")
            return AgentBayDelegateStrategy(session_mgr=session_mgr)
        case _:
            raise RuntimeError(f"未知 mode={mode!r}")
