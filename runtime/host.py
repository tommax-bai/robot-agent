"""
Host：进程级单例，负责组装 Worker + 共享服务。

三种拓扑：
- MONOLITH: Brain + Session 在一个进程，本地 Chrome 或完整 AgentBay。
- BRAIN:    只跑决策层，通过 RemoteEnv 调远程 Session Service。
- SESSION:  只跑云手机 session 管理，暴露 HTTP 给 Brain 调。

一个 Host 持有：
- events: EventBus（所有拓扑都需要）
- workers: dict[account_id -> Worker]
- MONOLITH/BRAIN 还会构造共享决策服务（LLM/Planner/SkillRegistry 等）

替代旧的：AppContainer + SessionAppContainer + WorkerPool + SessionWorker。
"""

from __future__ import annotations

from enum import StrEnum

import config
import utils.logger as logger
from runtime.events import EventBus
from runtime.wire import build_env, build_session, state_file_for
from runtime.worker import Worker


class Topology(StrEnum):
    MONOLITH = "monolith"
    BRAIN = "brain"
    SESSION = "session"


class Host:
    """进程级 DI 容器。"""

    _current: "Host | None" = None

    def __init__(self, topology: Topology = Topology.MONOLITH):
        self.topology = topology
        self.events = EventBus()
        self.workers: dict[str, Worker] = {}
        self._default_id: str | None = None

        # Brain / Monolith 需要的共享服务
        self.llm = None
        self.skills = None
        self.intents = None
        self.page_registry = None
        self.page_matcher = None
        self.page_classifier = None
        self.page_context_cache = None
        self.visual_locator = None
        self.recipe_store = None
        self.trace_recorder = None
        self.behavior_summarizer = None
        self.planner = None

        if topology in (Topology.MONOLITH, Topology.BRAIN):
            self._build_brain_services()

        self._maybe_cleanup_orphans()
        self._maybe_cleanup_old_files()
        self._build_workers()

    # ─── 启动 ─────────────────────────────────────────────────

    def _build_brain_services(self) -> None:
        from services.intents import IntentRegistry
        from services.recipes import BehaviorSummarizer, RecipeStore, TraceRecorder
        from services.skill_registry import SkillRegistry
        from services.vision import (
            LlmPageClassifier,
            LocalPageMatcher,
            PageContextCache,
            PageRegistry,
            VisualLocator,
        )
        from tools.llm import LlmTool
        from agents.planner import build_default_planner

        self.llm = LlmTool()
        self.skills = SkillRegistry("skills")
        self.intents = IntentRegistry()

        pc = config.settings.agent.page_classifier
        self.page_registry = PageRegistry(pc.registry_file)
        self.page_matcher = LocalPageMatcher(
            registry=self.page_registry,
            min_score=pc.local_match_min_score,
        )
        self.page_classifier = LlmPageClassifier(
            llm=self.llm,
            registry=self.page_registry,
            local_matcher=self.page_matcher,
        )
        self.page_context_cache = PageContextCache(
            classifier=self.page_classifier,
            background=pc.background_enabled,
            max_workers=pc.background_workers,
        )
        self.visual_locator = VisualLocator()
        self.recipe_store = RecipeStore()
        self.trace_recorder = TraceRecorder()
        self.behavior_summarizer = BehaviorSummarizer(llm=self.llm, intents=self.intents)
        self.planner = build_default_planner(skills=self.skills, intents=self.intents)

    def _maybe_cleanup_orphans(self) -> None:
        rt = config.settings.agent.runtime
        if (
            rt.mode != "local_chrome"
            and not rt.session_service_url
            and rt.auto_cleanup_orphans_on_startup
        ):
            from tools.cloud_mobile import cleanup_orphan_sessions
            cleanup_orphan_sessions(rt.agentbay.api_key)

    def _maybe_cleanup_old_files(self) -> None:
        if self.topology == Topology.SESSION:
            return
        cleanup = config.settings.agent.storage.cleanup
        if not cleanup.enabled:
            return
        from utils.fs import cleanup_old_files
        max_age = cleanup.max_age_days
        total = 0
        total += cleanup_old_files("data/action_traces", max_age_days=max_age, extensions=(".jsonl",))
        total += cleanup_old_files("history", max_age_days=max_age, extensions=(".md",))
        total += cleanup_old_files("data", max_age_days=max_age, extensions=(".jpeg", ".jpg"))
        if total > 0:
            logger.info({"msg": "启动清理旧数据文件完成", "deleted_files": total, "max_age_days": max_age})

    def _build_workers(self) -> None:
        raw_accounts = [a.model_dump() for a in config.settings.agent.accounts]
        accounts: list[dict | None] = raw_accounts or [None]
        for i, acct in enumerate(accounts):
            if acct is not None and not acct.get("id"):
                raise RuntimeError(f"agent.accounts[{i}] 缺少必填字段 'id'")
            worker = self._build_worker(acct)
            self.workers[worker.account_id] = worker
            if self._default_id is None or i == 0:
                self._default_id = worker.account_id

    def _build_worker(self, account_cfg: dict | None) -> Worker:
        account_id = (account_cfg or {}).get("id") or "default"
        mode = (account_cfg or {}).get("mode") or config.settings.agent.runtime.mode
        cfg = account_cfg or {}
        display_name = cfg.get("display_name", "")

        if self.topology == Topology.SESSION:
            # session-only：AgentBay 必建 session_mgr + env
            from tools.cloud_mobile import CloudMobileEnv, SessionManager

            ab = config.settings.agent.runtime.agentbay
            session_mgr = SessionManager(
                api_key=ab.api_key,
                image_id=cfg.get("image_id") or ab.image_id,
                idle_release_timeout=ab.idle_release_timeout,
                mode=mode,
                event_bus=self.events,
                account_id=account_id,
            )
            env = CloudMobileEnv(
                session_mgr=session_mgr,
                screenshot_format=cfg.get("screenshot_format") or ab.screenshot_format,
                mode=mode,
            )
            return Worker(
                account_id=account_id, mode=mode, session_mgr=session_mgr,
                env=env, account_cfg=account_cfg, display_name=display_name,
                events=self.events, minimal=True,
            )

        # MONOLITH / BRAIN：完整 Worker
        from services.state import JsonFileStateRepo

        state = JsonFileStateRepo(
            file_path=state_file_for(account_cfg),
            limits=config.settings.agent.state_limits.model_dump(),
        )
        session_mgr = build_session(mode, account_cfg, self.events, account_id)
        env = build_env(mode, account_cfg, session_mgr=session_mgr)
        return Worker(
            account_id=account_id,
            mode=mode,
            session_mgr=session_mgr,
            env=env,
            account_cfg=account_cfg,
            display_name=display_name,
            state=state,
            llm=self.llm,
            skills=self.skills,
            page_context_cache=self.page_context_cache,
            visual_locator=self.visual_locator,
            recipe_store=self.recipe_store,
            trace_recorder=self.trace_recorder,
            behavior_summarizer=self.behavior_summarizer,
            events=self.events,
            planner=self.planner,
        )

    # ─── 运行期增删 ───────────────────────────────────────────

    def add_worker(self, account_cfg: dict, *, start_scheduler: bool = True) -> Worker:
        if not account_cfg.get("id"):
            raise ValueError("account_cfg 缺少必填字段 'id'")
        if account_cfg["id"] in self.workers:
            raise ValueError(f"worker account_id={account_cfg['id']} 已存在")
        worker = self._build_worker(account_cfg)
        self.workers[worker.account_id] = worker
        if start_scheduler and worker.supervisor is not None:
            worker.supervisor.start()
        return worker

    def remove_worker(self, account_id: str) -> Worker:
        if account_id not in self.workers:
            raise KeyError(f"未注册的 worker account_id={account_id}")
        if len(self.workers) <= 1:
            raise RuntimeError("不能移除最后一个 worker")
        worker = self.workers[account_id]
        if worker.supervisor is not None and worker.supervisor.is_busy():
            snap = worker.supervisor.status()
            raise RuntimeError(
                f"worker={account_id} 当前任务 trace={snap.current_trace_id} 正在运行，"
                "请先取消任务再移除"
            )
        del self.workers[account_id]
        if self._default_id == account_id:
            self._default_id = next(iter(self.workers), None)
        if worker.supervisor is not None:
            try:
                worker.supervisor.request_stop("worker_removed")
            except Exception:
                pass
        try:
            worker.shutdown()
        except Exception:
            pass
        return worker

    # ─── 访问 ─────────────────────────────────────────────────

    def worker(self, account_id: str) -> Worker:
        if account_id not in self.workers:
            raise KeyError(f"未注册的 worker account_id={account_id}")
        return self.workers[account_id]

    def default(self) -> Worker:
        if self._default_id is None:
            raise RuntimeError("Host 中无任何 worker")
        return self.workers[self._default_id]

    def has(self, account_id: str) -> bool:
        return account_id in self.workers

    def list(self) -> list[Worker]:
        return list(self.workers.values())

    @property
    def default_id(self) -> str | None:
        return self._default_id

    # ─── 生命周期 ─────────────────────────────────────────────

    def shutdown_all(self) -> None:
        for w in self.workers.values():
            try:
                w.shutdown()
            except Exception:
                pass

    # ─── 单例入口 ─────────────────────────────────────────────

    @classmethod
    def boot(cls, topology: Topology = Topology.MONOLITH) -> "Host":
        if cls._current is None:
            cls._current = cls(topology)
        return cls._current

    @classmethod
    def current(cls) -> "Host":
        if cls._current is None:
            raise RuntimeError("Host 尚未初始化。请先调用 Host.boot() ")
        return cls._current

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._current = None


__all__ = ["Host", "Topology"]
