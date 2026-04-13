"""
AppContainer: 应用启动时的依赖注入容器。
所有 agent / service / tool 在此处构造并连线。
"""

from __future__ import annotations

import config
from agents.operator import (
    ActionDispatcher,
    AliyunMobileAgentStrategy,
    Operator,
    RecipeOperator,
    SubtaskRunner,
    SubtaskStrategy,
    VisionActionStep,
)
from agents.planner.planner import Planner
from agents.strategist import Strategist
from agents.supervisor.supervisor import Supervisor
from services.action_memory import BehaviorSummarizer, RecipeStore, TraceRecorder
from services.agent_state import AgentStateRepo, JsonFileStateRepo
from services.intent_registry import IntentRegistry
from services.skill_registry import SkillRegistry
from services.vision import LlmPageClassifier, LocalPageMatcher, PageContextCache, PageRegistry, VisualLocator
from tools.backends import ActionBackend, AgentBayBackend, MacOSChromeBackend
from tools.llm_tool import LlmTool


class AppContainer:
    """
    应用级单例：在 app.py 启动时构造一次。
    持有所有 agent + service + tool 的实例引用。
    """

    def __init__(self):
        # ── 基础设施 ───────────────────────────────────────────
        self.state: AgentStateRepo = JsonFileStateRepo(
            file_path=config.agent["storage"]["state_file"],
            limits=config.agent["state_limits"],
        )
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
        # 执行后端：按 agent.runtime.mode 选择
        self.backend: ActionBackend = _build_backend()

        # ── Agents (按依赖顺序) ────────────────────────────────
        self.strategist = Strategist(state=self.state, llm=self.llm)
        self.planner = Planner(skills=self.skills, intents=self.intents)
        self.dispatcher = ActionDispatcher(skills=self.skills, backend=self.backend)
        self.vision_step = VisionActionStep(
            skills=self.skills,
            dispatcher=self.dispatcher,
            backend=self.backend,
            recorder=self.trace_recorder,
            page_cache=self.page_context_cache,
        )
        self.recipe_operator = RecipeOperator(
            dispatcher=self.dispatcher,
            recipes=self.recipe_store,
            page_cache=self.page_context_cache,
            locator=self.visual_locator,
            backend=self.backend,
            recorder=self.trace_recorder,
        )
        # 子任务执行策略：local_chrome / cloud_vision 都用自家视觉循环；
        # cloud_aliyun 把整个子任务委托给阿里 mobile_use Agent。
        self.strategy: SubtaskStrategy = _build_strategy(
            backend=self.backend,
            vision_step=self.vision_step,
        )
        self.subtask_runner = SubtaskRunner(
            strategy=self.strategy,
            recipe_operator=self.recipe_operator,
            behavior_summarizer=self.behavior_summarizer,
            max_resumes=2,
        )
        self.operator = Operator(
            planner=self.planner,
            runner=self.subtask_runner,
        )
        self.supervisor = Supervisor(
            operator=self.operator,
            strategist=self.strategist,
            state=self.state,
            llm=self.llm,
        )


def _build_backend() -> ActionBackend:
    """根据 config.agent.runtime.mode 构造对应后端。

    cloud_aliyun 模式底层仍需 AgentBay session 来执行原子动作（recipe 快路径
    + Strategy 兜底场景），所以同样返回 AgentBayBackend；与 cloud_vision 的
    差异在 _build_strategy 中体现。
    """
    runtime_cfg = config.agent["runtime"]
    mode = runtime_cfg["mode"]

    match mode:
        case "local_chrome":
            return MacOSChromeBackend()
        case "cloud_vision" | "cloud_aliyun":
            ab = runtime_cfg["agentbay"]
            return AgentBayBackend(
                api_key=ab["api_key"],
                image_id=ab["image_id"],
                screenshot_format=ab["screenshot_format"],
            )
        case _:
            raise RuntimeError(
                f"未知 agent.runtime.mode={mode!r}，可选值: local_chrome / cloud_vision / cloud_aliyun"
            )


def _build_strategy(
    *,
    backend: ActionBackend,
    vision_step: VisionActionStep,
) -> SubtaskStrategy:
    """根据 mode 选择子任务执行策略。

    - local_chrome / cloud_vision: 自家 VLM 视觉循环（VisionActionStep）
    - cloud_aliyun: 委托给阿里 mobile_use Agent
    """
    mode = config.agent["runtime"]["mode"]

    match mode:
        case "local_chrome" | "cloud_vision":
            return vision_step
        case "cloud_aliyun":
            if not isinstance(backend, AgentBayBackend):
                raise RuntimeError(
                    f"cloud_aliyun 模式必须使用 AgentBayBackend，实际为 {type(backend).__name__}"
                )
            return AliyunMobileAgentStrategy(backend=backend)
        case _:
            raise RuntimeError(f"未知 agent.runtime.mode={mode!r}")


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
