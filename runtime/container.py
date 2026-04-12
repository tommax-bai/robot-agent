"""
AppContainer: 应用启动时的依赖注入容器。
所有 agent / service / tool 在此处构造并连线。
"""

from __future__ import annotations

import config
from agents.operator import (
    ActionDispatcher,
    Operator,
    RecipeOperator,
    SubtaskRunner,
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

        # ── Agents (按依赖顺序) ────────────────────────────────
        self.strategist = Strategist(state=self.state, llm=self.llm)
        self.planner = Planner(skills=self.skills, intents=self.intents)
        self.dispatcher = ActionDispatcher(skills=self.skills)
        self.vision_step = VisionActionStep(
            skills=self.skills,
            dispatcher=self.dispatcher,
            recorder=self.trace_recorder,
            page_cache=self.page_context_cache,
        )
        self.recipe_operator = RecipeOperator(
            dispatcher=self.dispatcher,
            recipes=self.recipe_store,
            page_cache=self.page_context_cache,
            locator=self.visual_locator,
            recorder=self.trace_recorder,
        )
        self.subtask_runner = SubtaskRunner(
            vision_step=self.vision_step,
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
