"""SubtaskObserver：子任务完成后的副作用接入点。

把原 SubtaskRunner 内联的 `behavior_summarizer.submit_trace(...)` 抽成
可插拔 Observer 列表。未来加 metrics、tracing、slack 通知等也在这里接。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agents.base import SubTask, TaskResult
    from runtime.ctx import RunContext
    from services.recipes import BehaviorSummarizer


class SubtaskObserver(Protocol):
    def on_complete(self, subtask: SubTask, result: TaskResult, ctx: RunContext) -> None: ...


class BehaviorSummarizerObserver:
    """子任务成功后把 trace 丢给 BehaviorSummarizer 去异步总结行为。"""

    def __init__(self, summarizer: BehaviorSummarizer):
        self._summarizer = summarizer

    def on_complete(self, subtask: SubTask, result: TaskResult, ctx: RunContext) -> None:
        if result.ok:
            self._summarizer.submit_trace(
                ctx.trace_id, subtask.id, subtask.intent or "unknown"
            )
