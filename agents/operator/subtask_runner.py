"""SubtaskRunner：串联 Resolvers → 通知 Observers → 返回 TaskResult。

与旧实现的关键差异：
- recipe / strategy 不再用 if/else 硬编码，都成了 SubtaskResolver，
  Runner 按顺序遍历，首个 resolved 或 failed 胜出，skipped 继续下一个。
- 续航重试从 Runner 搬进 RetryPolicy，由 StrategyResolver 持有。
- behavior_summarizer 副作用从 Runner 搬出到 SubtaskObserver 列表。
- `set_strategy` 保留，内部转发到 StrategyResolver.set_strategy（hot-swap 场景）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import AgentError, ControlSignal, TaskResult

if TYPE_CHECKING:
    from agents.base import SubTask
    from agents.operator.recipe_operator import RecipeOperator
    from agents.operator.resolver import SubtaskResolver
    from agents.operator.resolvers.strategy import StrategyResolver
    from agents.operator.strategy import SubtaskStrategy
    from runtime.ctx import RunContext
    from agents.operator.observer import SubtaskObserver
    from services.recipes import BehaviorSummarizer


class SubtaskRunner:
    name = "subtask_runner"

    def __init__(
        self,
        resolvers: list[SubtaskResolver],
        *,
        observers: list[SubtaskObserver] | None = None,
        strategy_resolver: StrategyResolver | None = None,
    ):
        if not resolvers:
            raise ValueError("SubtaskRunner 至少需要 1 个 resolver")
        self._resolvers = resolvers
        self._observers = observers or []
        # 显式保留对 StrategyResolver 的引用，方便 hot-swap 时替换底层 strategy
        # 而不用重建整个 Resolver 链。
        self._strategy_resolver = strategy_resolver

    def set_strategy(self, strategy: SubtaskStrategy) -> None:
        if self._strategy_resolver is None:
            raise RuntimeError(
                "SubtaskRunner 构造时没有传 strategy_resolver，无法 hot-swap strategy"
            )
        self._strategy_resolver.set_strategy(strategy)

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        last_skip_reason = ""
        for resolver in self._resolvers:
            outcome = await resolver.resolve(subtask, ctx)
            if outcome.kind == "skipped":
                last_skip_reason = outcome.reason
                continue
            if outcome.result is None:
                # 防御：resolved / failed 必须带 result
                raise RuntimeError(
                    f"resolver={resolver.name} kind={outcome.kind} 但未提供 result"
                )
            self._notify(subtask, outcome.result, ctx)
            return outcome.result

        # 所有 Resolver 都 skipped，正常不应发生（StrategyResolver 永不 skip）。
        logger.error(
            {"msg": "没有 Resolver 接手子任务", "last_skip": last_skip_reason},
            ctx.trace_id,
        )
        return TaskResult.failure(AgentError(f"no resolver handled subtask: {last_skip_reason}"))

    @property
    def strategy_resolver(self) -> StrategyResolver | None:
        return self._strategy_resolver

    def _notify(self, subtask: SubTask, result: TaskResult, ctx: RunContext) -> None:
        for obs in self._observers:
            try:
                obs.on_complete(subtask, result, ctx)
            except ControlSignal:
                # 任务取消/预算耗尽等生命周期信号必须穿透 observer 回到主循环
                raise
            except Exception as e:
                logger.warning(
                    {"msg": "subtask observer 异常（不阻塞主流程）", "error": str(e)},
                    ctx.trace_id,
                )


def build_default_subtask_runner(
    *,
    strategy: SubtaskStrategy,
    recipe_operator: RecipeOperator,
    behavior_summarizer: BehaviorSummarizer,
    max_resumes: int = 2,
) -> SubtaskRunner:
    """默认 wiring：[RecipeResolver, StrategyResolver] + BehaviorSummarizerObserver。

    hot-swap strategy 走 runner.set_strategy(new_strategy)，内部保留
    StrategyResolver 引用以便原位替换底层 strategy。
    """
    from agents.operator.observer import BehaviorSummarizerObserver
    from agents.operator.resolvers.recipe import RecipeResolver
    from agents.operator.resolvers.strategy import StrategyResolver
    from agents.operator.retry_policy import ResumeOnBudgetPolicy

    strategy_resolver = StrategyResolver(
        strategy=strategy,
        retry=ResumeOnBudgetPolicy(max_resumes=max_resumes),
    )
    return SubtaskRunner(
        resolvers=[RecipeResolver(recipe_operator), strategy_resolver],
        observers=[BehaviorSummarizerObserver(behavior_summarizer)],
        strategy_resolver=strategy_resolver,
    )
