"""
LlmTool: agent 框架使用的 LLM 调用门面。

封装重试、Token 记录、不同模式（JSON/文本/视觉），提供干净的方法签名。

特性：
- with_trace(trace_id) 派生绑定，让调用方少传一个参数
- MockLlmTool 子类用于测试
"""
from __future__ import annotations

from typing import Any

from tools import llm_caller


class LlmTool:
    """
    真实的 LLM 调用实现。委托给 tools.llm_caller。
    可通过 with_trace 派生一个绑定 trace_id 的子实例。
    """

    def __init__(self, default_trace_id: str = "system"):
        self._default_trace_id = default_trace_id

    def with_trace(self, trace_id: str) -> LlmTool:
        """派生一个绑定指定 trace_id 的子实例（轻量，仅记 trace_id）"""
        return self.__class__(default_trace_id=trace_id)

    def call_json(
        self,
        messages: list[dict],
        model: str,
        client_name: str,
        trace_id: str | None = None,
        **kwargs,
    ) -> tuple[Any, str]:
        return llm_caller.call_llm(
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
        return llm_caller.call_llm_text(
            messages=messages,
            model=model,
            client_name=client_name,
            trace_id=trace_id or self._default_trace_id,
            **kwargs,
        )

    def call_with_template(
        self, meta: dict, prompt: str, default_temperature: float = 0.9
    ) -> str:
        return llm_caller.call_llm_with_template(meta, prompt, default_temperature)


class MockLlmTool(LlmTool):
    """
    测试用 mock：返回预设的响应序列。
    每次调用按顺序消费一个响应，到尽头时复用最后一个。
    继承 LlmTool 以满足类型约束。
    """

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
        return self  # mock 不需要派生

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
