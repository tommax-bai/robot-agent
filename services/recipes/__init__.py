"""services.recipes: GUI 动作 recipe 系统。

组成：
- model —— Recipe / RecipeStep / RecipeAttempt 数据结构
- repo  —— RecipeStore：正式/trial 两层仓储、匹配、trial 生命周期
- recorder —— TraceRecorder：LLM 决策轨迹追加记录
- miner —— RecipeMiner：从成功轨迹挖掘候选 recipe（LLM 旁路）
"""

from __future__ import annotations

from services.recipes.miner import BehaviorSummarizer
from services.recipes.model import ActionRecipe, RecipeAttempt, RecipeStep
from services.recipes.recorder import TraceRecorder, observation_hash
from services.recipes.repo import RecipeStore

__all__ = [
    "ActionRecipe",
    "BehaviorSummarizer",
    "RecipeAttempt",
    "RecipeStep",
    "RecipeStore",
    "TraceRecorder",
    "observation_hash",
]
