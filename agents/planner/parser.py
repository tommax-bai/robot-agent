"""PlanParser：LLM 原始 JSON 输出 → list[raw_task_dict]。

只做顶层结构校验（data 是 dict、tasks 是非空 list）。
逐项的 sub_goal / required_skill / intent 字段由 SubTaskNormalizer 处理。

失败抛 PlannerError，并把原始 LLM 文本 attach 到 __cause__ 链里方便调试。
"""

from __future__ import annotations

from typing import Any

from agents.base import PlannerError


class PlanParser:
    def parse(self, data: Any, raw_text: str) -> list[dict]:
        if not isinstance(data, dict):
            raise PlannerError(
                f"planner 返回的顶层不是 dict: {type(data).__name__}，raw={raw_text[:200]!r}"
            )
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlannerError(f"planner 返回了无效的 tasks 字段: {data}")
        return [t for t in raw_tasks if isinstance(t, dict)]
