"""
AppContainer: 应用启动时的依赖注入容器。
所有 agent / service / tool 在此处构造并连线。
"""
from __future__ import annotations

import config
from agents.operator import (
    ActionDispatcher,
    Operator,
    SubtaskRunner,
    VisionActionStep,
)
from agents.planner.planner import Planner
from agents.strategist import Strategist
from agents.supervisor.supervisor import Supervisor
from services.agent_state import JsonFileStateRepo, AgentStateRepo
from services.skill_registry import SkillRegistry
from tools.llm_tool import LlmTool


class AppContainer:
    """
    应用级单例：在 app.py 启动时构造一次。
    持有所有 agent + service + tool 的实例引用。
    """

    def __init__(self):
        # ── 基础设施 ───────────────────────────────────────────
        self.state: AgentStateRepo = JsonFileStateRepo(
            file_path=config.agent["maintenance"]["state_file"],
            limits=config.agent["state_limits"],
        )
        self.llm = LlmTool()
        self.skills = SkillRegistry("skills")

        # ── Agents (按依赖顺序) ────────────────────────────────
        self.strategist = Strategist(state=self.state, llm=self.llm)
        self.planner = Planner(skills=self.skills)
        self.dispatcher = ActionDispatcher(skills=self.skills)
        self.vision_step = VisionActionStep(
            skills=self.skills,
            dispatcher=self.dispatcher,
        )
        self.subtask_runner = SubtaskRunner(vision_step=self.vision_step, max_resumes=2)
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
