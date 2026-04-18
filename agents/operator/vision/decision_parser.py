"""DecisionParser：把 LLM 解析后的对象（dict / list）转成 Decision。

把原 `Decision.parse` 分类方法搬出来单独成类，理由：
- 职责隔离：Decision 是纯值对象，解析是算法，两者生命周期不同
- 未来 LLM 输出格式再变（第 5 种、第 6 种形态），改这一个文件即可
- 失败路径（DecisionParseError）在解析器里集中抛，Decision 不需要懂这些"""

from __future__ import annotations

from typing import Any

from agents.base import DecisionParseError
from agents.operator.vision.types import Action, Decision


class DecisionParser:
    """把 LLM 输出（已解析成 Python 对象）转成 Decision。"""

    @classmethod
    def parse(cls, llm_output: Any, raw_text: str) -> Decision:
        """
        从 LLM 解析后的对象（dict 或 list）构造 Decision。
        在这一层做所有的脏数据清洗：键名归一化、字段名变体、坐标键名兜底等。
        失败抛 DecisionParseError。
        """
        actions_raw, thought = cls._extract_actions_and_thought(llm_output)
        if not actions_raw:
            raise DecisionParseError(f"未从 LLM 输出中识别到任何 action: {raw_text[:200]}")

        actions: list[Action] = []
        for a in actions_raw:
            if not isinstance(a, dict) or "method" not in a:
                continue
            params = cls._normalize_params(a.get("params") or {})
            method = a["method"]
            if method in ("click", "dblclick", "move") and "x" not in params:
                # LLM 偶尔输出带空格或引号的键名
                for k, v in list(params.items()):
                    lk = k.lower()
                    if "x" in lk:
                        params["x"] = v
                    if "y" in lk:
                        params["y"] = v
            actions.append(Action(method=method, params=params, delay=a.get("delay")))

        if not actions:
            raise DecisionParseError(f"所有 action 都缺少 method: {raw_text[:200]}")

        return Decision(thought=thought, actions=actions, raw=raw_text)

    @staticmethod
    def _extract_actions_and_thought(obj: Any) -> tuple[list[dict], str]:
        """支持四种 LLM 输出形态"""
        if isinstance(obj, list):
            thought = obj[0].get("thought", "") if obj and isinstance(obj[0], dict) else ""
            if obj and isinstance(obj[0], dict) and isinstance(obj[0].get("actions"), list):
                return [a for a in obj[0]["actions"] if isinstance(a, dict)], thought
            return [a for a in obj if isinstance(a, dict)], thought
        if not isinstance(obj, dict):
            return [], ""
        thought = obj.get("thought", "")
        if obj.get("method") == "execute_sequence":
            inner = obj.get("params", {}).get("actions", [])
            return [a for a in inner if isinstance(a, dict)], thought
        if "actions" in obj and isinstance(obj["actions"], list):
            return [a for a in obj["actions"] if isinstance(a, dict)], thought
        if "method" in obj:
            return [obj], thought
        return [], thought

    @staticmethod
    def _normalize_params(params: dict) -> dict:
        if not isinstance(params, dict):
            return {}
        return {k.strip().strip('"').strip("'").strip(): v for k, v in params.items()}
