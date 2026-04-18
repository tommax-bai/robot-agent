"""
LLM 服务门面：`LlmTool` 给 agent 层用，`MockLlmTool` 给测试用。

`with_trace(trace_id)` 派生轻量绑定实例，让调用方少传一个参数。
"""

from __future__ import annotations

from typing import Any

from tools.llm import caller


class LlmTool:
    """真实 LLM 调用入口。委托给 `tools.llm.caller`。"""

    def __init__(self, default_trace_id: str = "system"):
        self._default_trace_id = default_trace_id

    def with_trace(self, trace_id: str) -> LlmTool:
        """派生绑定指定 trace_id 的子实例（轻量，仅记 trace_id）。"""
        return self.__class__(default_trace_id=trace_id)

    def call_json(
        self,
        messages: list[dict],
        model: str,
        client_name: str,
        trace_id: str | None = None,
        **kwargs,
    ) -> tuple[Any, str]:
        return caller.call_json(
            messages=messages,
            model=model,
            client_name=client_name,
            trace_id=trace_id or self._default_trace_id,
            **kwargs,
        )

    def call_text(
        self,
        messages: list[dict],
        model: str,
        client_name: str,
        trace_id: str | None = None,
        **kwargs,
    ) -> str:
        return caller.call_text(
            messages=messages,
            model=model,
            client_name=client_name,
            trace_id=trace_id or self._default_trace_id,
            **kwargs,
        )

    def call_with_template(self, meta: dict, prompt: str, default_temperature: float = 0.9) -> str:
        return caller.call_with_template(meta, prompt, default_temperature)


class MockLlmTool(LlmTool):
    """测试用 mock：返回预设的响应序列，超出时复用最后一个。"""

    def __init__(
        self,
        json_responses: list[tuple[Any, str]] | None = None,
        text_responses: list[str] | None = None,
    ):
        super().__init__(default_trace_id="mock")
        self._json_responses = json_responses or []
        self._text_responses = text_responses or []
        self._json_idx = 0
        self._text_idx = 0
        self.call_log: list[dict] = []

    def with_trace(self, trace_id: str) -> MockLlmTool:
        return self

    def call_json(self, messages, model, client_name, trace_id=None, **kwargs):
        self.call_log.append({"mode": "json", "model": model, "trace_id": trace_id})
        if not self._json_responses:
            raise RuntimeError("MockLlmTool: 没有预设 JSON 响应")
        idx = min(self._json_idx, len(self._json_responses) - 1)
        self._json_idx += 1
        return self._json_responses[idx]

    def call_text(self, messages, model, client_name, trace_id=None, **kwargs):
        self.call_log.append({"mode": "text", "model": model, "trace_id": trace_id})
        if not self._text_responses:
            raise RuntimeError("MockLlmTool: 没有预设 text 响应")
        idx = min(self._text_idx, len(self._text_responses) - 1)
        self._text_idx += 1
        return self._text_responses[idx]

    def call_with_template(self, meta, prompt, default_temperature=0.9):
        return self.call_text([{"role": "user", "content": prompt}], "mock", "mock")


__all__ = ["LlmTool", "MockLlmTool"]
