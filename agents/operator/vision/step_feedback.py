"""StepFeedback：把 VisionActionStep 里 5 个散落的 static formatter 聚拢。

职责：
- 观察图像哈希（用来判断页面是否变化）
- 上一步结果 / 动作的文本化
- 本轮给 LLM 的"上一步发生了什么"反馈段落
- 单个 action 的简要日志描述

这些都是纯函数 / 无状态工具，分出来后可单测，也让 VisionActionStep 只关心主循环。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.operator.vision.types import Action, ActionOutcome, Decision, Observation


class StepFeedback:
    @staticmethod
    def hash_observation(observation: Observation) -> str:
        return hashlib.sha1(observation.image_base64.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def format_last_outcome(outcome: ActionOutcome | None) -> str:
        if outcome is None:
            return json.dumps({"ok": True, "message": "任务开始"}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": all(r.ok for r in outcome.results),
                "results": [
                    {"method": r.method, "ok": r.ok, "message": r.message} for r in outcome.results
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def build_feedback(
        cls,
        *,
        last_decision: Decision | None,
        last_outcome: ActionOutcome | None,
        last_observation_hash: str,
        current_observation_hash: str,
    ) -> str:
        if last_decision is None or last_outcome is None:
            return ""
        lines = [f"上一步动作: {cls._format_last_actions(last_decision)}"]
        if last_observation_hash and current_observation_hash == last_observation_hash:
            lines.append(
                "页面变化判断: 上一步动作执行后截图没有变化。"
                "这通常说明点击位置不对、目标元素不可点击，或页面没有响应。"
                "不要重复同一坐标；请重新根据当前截图定位，必要时换搜索入口或使用工具。"
            )
        elif last_observation_hash:
            lines.append("页面变化判断: 上一步动作执行后截图发生变化，请基于当前截图重新判断。")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_last_actions(decision: Decision) -> str:
        actions: list[dict[str, Any]] = []
        for action in decision.actions:
            p = action.params
            actions.append(
                {
                    "method": action.method,
                    "x": p.get("x"),
                    "y": p.get("y"),
                    "description": p.get("description"),
                }
            )
        return json.dumps(actions, ensure_ascii=False)

    @staticmethod
    def format_action_brief(action: Action) -> str:
        p = action.params
        desc = p.get("description", "")
        if action.method in ("click", "dblclick", "move"):
            return f"{action.method}({p.get('x')},{p.get('y')} {desc})"
        if action.method == "scroll":
            return f"scroll({p.get('clicks')} {desc})"
        if action.method == "paste":
            text = p.get("text", "")
            preview = text[:20] + "…" if len(text) > 20 else text
            return f'paste("{preview}")'
        if action.method == "hotkey":
            return f"hotkey({p.get('keys')})"
        if action.method == "finish":
            return f"finish({p.get('summary', '')[:40]})"
        if action.method == "wait":
            return f"wait({p.get('milliseconds')}ms)"
        if action.method == "drag":
            return f"drag({p.get('x1')},{p.get('y1')}→{p.get('x2')},{p.get('y2')} {desc})"
        return f"{action.method}({desc})"
