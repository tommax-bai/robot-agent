"""
AppContainer: 应用启动时的依赖注入容器。

职责拆分：
- 共享层（self.* 直接持有）：LLM 客户端、技能/意图/页面词表、recipe 仓库、
  事件总线、Planner 等所有 worker 共用的资源
- Worker 层（self.workers）：每个账号一份独立的 backend / dispatcher /
  vision_step / recipe_operator / strategy / subtask_runner / operator /
  supervisor / state，由 WorkerPool 统一管理。支持运行时动态增删。

向后兼容：`container.supervisor` / `container.backend` / `container.strategy`
等老属性以 @property shortcut 形式保留，转发到 `workers.default()`，
老代码（早期 API 路由、测试脚本）零改动也能跑。
"""

from __future__ import annotations

import config
from agents.planner.planner import Planner
from runtime.event_bus import EventBus
from runtime.worker import Worker, build_backend, build_session_mgr
from runtime.worker_pool import WorkerPool
from services.action_memory import BehaviorSummarizer, RecipeStore, TraceRecorder
from services.agent_state import JsonFileStateRepo
from services.intent_registry import IntentRegistry
from services.skill_registry import SkillRegistry
from services.vision import LlmPageClassifier, LocalPageMatcher, PageContextCache, PageRegistry, VisualLocator
from tools.llm_tool import LlmTool


class AppContainer:
    """应用级单例：在 app.py 启动时构造一次。"""

    def __init__(self):
        # ── 共享基础设施（所有 worker 共用） ────────────────────────
        self.llm = LlmTool()
        self.skills = SkillRegistry("skills")
        self.intents = IntentRegistry()
        page_classifier_cfg = config.agent["page_classifier"]
        self.page_registry = PageRegistry(page_classifier_cfg["registry_file"])
        self.page_matcher = LocalPageMatcher(
            registry=self.page_registry,
            min_score=page_classifier_cfg["local_match_min_score"],
        )
        self.page_classifier = LlmPageClassifier(
            llm=self.llm,
            registry=self.page_registry,
            local_matcher=self.page_matcher,
        )
        self.page_context_cache = PageContextCache(
            classifier=self.page_classifier,
            background=page_classifier_cfg["background_enabled"],
            max_workers=page_classifier_cfg["background_workers"],
        )
        self.visual_locator = VisualLocator()
        self.recipe_store = RecipeStore()
        self.trace_recorder = TraceRecorder()
        self.behavior_summarizer = BehaviorSummarizer(llm=self.llm, intents=self.intents)
        self.event_bus = EventBus()
        self.planner = Planner(skills=self.skills, intents=self.intents)

        # ── 启动时孤儿 session 清理（防 SIGKILL 留下未释放实例无限计费）
        # 仅 cloud 模式 + 配置开启时执行；本地 Chrome 模式无 session 概念
        runtime_cfg = config.agent["runtime"]
        # remote 模式下由 Session Service 负责孤儿清理
        if (
            runtime_cfg["mode"] != "local_chrome"
            and not runtime_cfg.get("session_service_url")
            and runtime_cfg.get("auto_cleanup_orphans_on_startup")
        ):
            from tools.backends import cleanup_orphan_sessions

            cleanup_orphan_sessions(runtime_cfg["agentbay"]["api_key"])

        # ── 启动时旧数据文件清理 ─────────────────────────────────
        cleanup_cfg = config.agent["storage"].get("cleanup", {})
        if cleanup_cfg.get("enabled", False):
            from utils.data_cleanup import cleanup_old_files

            max_age = cleanup_cfg.get("max_age_days", 7)
            total_cleaned = 0
            total_cleaned += cleanup_old_files("data/action_traces", max_age_days=max_age, extensions=(".jsonl",))
            total_cleaned += cleanup_old_files("history", max_age_days=max_age, extensions=(".md",))
            total_cleaned += cleanup_old_files("data", max_age_days=max_age, extensions=(".jpeg", ".jpg"))
            if total_cleaned > 0:
                import utils.logger as logger
                logger.info({"msg": "启动清理旧数据文件完成", "deleted_files": total_cleaned, "max_age_days": max_age})

        # ── Worker 池 ─────────────────────────────────────────────
        # config.agent["accounts"] 非空 → 每项一个 worker
        # 空 → 退化为单 default worker，使用全局 storage / runtime 配置
        self.workers = WorkerPool()
        accounts_cfg = config.agent.get("accounts") or []
        if accounts_cfg:
            for i, acct in enumerate(accounts_cfg):
                if not acct.get("id"):
                    raise RuntimeError(f"agent.accounts[{i}] 缺少必填字段 'id'")
                self.workers.add(self._build_worker(acct), default=(i == 0))
        else:
            self.workers.add(self._build_worker(None), default=True)

    def _build_worker(self, account_cfg: dict | None) -> Worker:
        account_id = (account_cfg or {}).get("id") or "default"
        # 新 worker 继承全局 config 里的 mode 作为起始模式；之后 swap 只影响自己
        mode = (account_cfg or {}).get("mode") or config.agent["runtime"]["mode"]
        state_file = self._state_file_for(account_cfg)
        state = JsonFileStateRepo(file_path=state_file, limits=config.agent["state_limits"])
        session_mgr = build_session_mgr(mode, account_cfg, self.event_bus, account_id)
        backend = build_backend(mode, account_cfg, session_mgr=session_mgr)
        return Worker(
            account_id=account_id,
            mode=mode,
            state=state,
            session_mgr=session_mgr,
            backend=backend,
            llm=self.llm,
            skills=self.skills,
            page_context_cache=self.page_context_cache,
            visual_locator=self.visual_locator,
            recipe_store=self.recipe_store,
            trace_recorder=self.trace_recorder,
            behavior_summarizer=self.behavior_summarizer,
            event_bus=self.event_bus,
            planner=self.planner,
            account_cfg=account_cfg,
        )

    @staticmethod
    def _state_file_for(account_cfg: dict | None) -> str:
        """决定 state 文件路径：
        - 显式 account_cfg.state_file 优先
        - 否则按账号 id 隔离到 data/accounts/{id}/agent_state.json
        - 单 default worker 退回到 storage.state_file（保持单账号项目兼容）
        """
        if account_cfg:
            if account_cfg.get("state_file"):
                return account_cfg["state_file"]
            return f"data/accounts/{account_cfg['id']}/agent_state.json"
        return config.agent["storage"]["state_file"]

    def add_worker(self, account_cfg: dict, *, start_scheduler: bool = True) -> Worker:
        """运行时新增一个 worker（dashboard +Add 按钮触发）。

        - account_cfg.id 必填且不能与现有 worker 冲突
        - 默认自动启动该 worker 的 scheduler（与 lifespan 行为一致）
        - 注意：动态新增不写回 config.agent.accounts，进程重启后消失
        """
        if not account_cfg.get("id"):
            raise ValueError("account_cfg 缺少必填字段 'id'")
        worker = self._build_worker(account_cfg)
        self.workers.add(worker, default=False)
        if start_scheduler:
            worker.supervisor.start_scheduler()
        return worker

    def remove_worker(self, account_id: str) -> Worker:
        """运行时移除 worker，并 release 其云端 session。

        失败场景由 WorkerPool.remove 抛出：
        - KeyError: 不存在
        - RuntimeError: 是最后一个 / 当前任务活跃
        """
        worker = self.workers.remove(account_id)
        # 停 scheduler 循环
        try:
            import asyncio as _aio

            if worker.supervisor._scheduler_task and not worker.supervisor._scheduler_task.done():
                worker.supervisor._scheduler_task.cancel()
        except Exception:
            pass
        # 释放云端 session
        try:
            worker.shutdown()
        except Exception:
            pass
        return worker

    # ─── 向后兼容 shortcut（指向 default worker） ─────────────────
    # 老代码通过 container.supervisor / .backend 访问，这些 property 保证不破坏

    @property
    def default_worker(self) -> Worker:
        return self.workers.default()

    @property
    def supervisor(self):
        return self.default_worker.supervisor

    @property
    def backend(self):
        return self.default_worker.backend

    @property
    def strategy(self):
        return self.default_worker.strategy

    @property
    def dispatcher(self):
        return self.default_worker.dispatcher

    @property
    def vision_step(self):
        return self.default_worker.vision_step

    @property
    def recipe_operator(self):
        return self.default_worker.recipe_operator

    @property
    def subtask_runner(self):
        return self.default_worker.subtask_runner

    @property
    def operator(self):
        return self.default_worker.operator

    @property
    def strategist(self):
        return self.default_worker.strategist

    @property
    def state(self):
        return self.default_worker.state

    # ─── runtime mode hot-swap：转发到 default worker（向后兼容）──
    # 真正想切某个特定 worker 的 mode 应该直接调 worker.swap_runtime_mode，
    # API 层的 POST /agent/runtime/mode 已经支持 account_id 参数。

    async def swap_runtime_mode(self, new_mode: str) -> dict:
        """转发到 default worker（老代码兼容入口）。"""
        return await self.default_worker.swap_runtime_mode(new_mode)


# 模块级单例（启动后由 app.py 设置）
_container: AppContainer | None = None


def get_container() -> AppContainer:
    if _container is None:
        raise RuntimeError("AppContainer 尚未初始化。请在 app.py lifespan 中调用 init_container()")
    return _container


def init_container() -> AppContainer:
    global _container
    if _container is None:
        _container = AppContainer()
    return _container
