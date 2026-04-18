"""TaskResult 聚合策略。

原来写在 `TaskResult.aggregate` 里的默认策略（"全 ok 才 ok / 取最后 summary /
取最后 error"）被独立出来，未来需要"有一个失败整体就失败"、"提取所有 error
拼接"等其他策略时只要再写一个 Policy 即可，不用去改 TaskResult 值对象。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from agents.base import TaskResult

if TYPE_CHECKING:
    pass


class AggregationPolicy(Protocol):
    def combine(self, results: list[TaskResult]) -> TaskResult: ...


class AllOkPolicy:
    """默认策略：全部子任务成功才算成功；summary/error 取最后一个非空值。"""

    def combine(self, results: list[TaskResult]) -> TaskResult:
        all_ok = all(r.ok for r in results)
        last_summary = next((r.summary for r in reversed(results) if r.summary), "")
        last_error = next((r.error for r in reversed(results) if r.error), None)
        return TaskResult(
            ok=all_ok,
            summary=last_summary,
            sub_results=results,
            error=last_error,
        )


_DEFAULT_POLICY = AllOkPolicy()


def aggregate_results(
    results: list[TaskResult],
    policy: AggregationPolicy | None = None,
) -> TaskResult:
    return (policy or _DEFAULT_POLICY).combine(results)
