"""
LLM 调用底层：HTTP + 重试 + token log。

所有 agent（operator/planner/strategist）通过 `LlmTool`（facade）间接走这里。
"""

from __future__ import annotations

import time

import config
from services import token_usage
from tools.llm import client as llm_client
from utils.events import Ev, ev
from utils.json_parse import extract_json


def call_json(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    **kwargs,
) -> tuple[dict, str]:
    """JSON 模式。返回 (parsed_json, raw_text)。"""
    raw = _call_raw(
        messages,
        model,
        client_name,
        trace_id,
        response_format={"type": "json_object"},
        **kwargs,
    )
    return extract_json(raw), raw


def call_text(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    **kwargs,
) -> str:
    """纯文本模式。"""
    return _call_raw(messages, model, client_name, trace_id, **kwargs)


def call_with_template(meta: dict, prompt: str, default_temperature: float = 0.9) -> str:
    """根据 prompt 模板 meta 的 provider/model/temperature 调 LLM（文本模式）。"""
    planner_cfg = config.settings.agent.planner
    client_name = meta.get("provider", planner_cfg.llm_client)
    model_name = meta.get("model", planner_cfg.model)
    temperature = meta.get("temperature", default_temperature)

    extras = {k: v for k, v in meta.items() if k not in ("provider", "model", "temperature")}

    return call_text(
        messages=[{"role": "user", "content": prompt}],
        model=model_name,
        client_name=client_name,
        temperature=temperature,
        **extras,
    )


def _call_raw(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    response_format: dict | None = None,
    **kwargs,
) -> str:
    max_retries = 3
    retry_delay = 2
    started = time.monotonic()

    for attempt in range(max_retries):
        try:
            client = llm_client.get_client(client_name)

            params = {
                "model": model,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.0),
            }
            if response_format:
                params["response_format"] = response_format
            if "max_tokens" in kwargs:
                params["max_tokens"] = kwargs["max_tokens"]

            response = client.chat.completions.create(**params)
            elapsed_ms = int((time.monotonic() - started) * 1000)

            usage = response.usage
            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            if usage:
                token_usage.record(
                    trace_id=trace_id,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

            ev(Ev.LLM_CALLED, trace_id=trace_id, model=model, client=client_name,
               elapsed_ms=elapsed_ms, attempt=attempt,
               prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
               total_tokens=prompt_tokens + completion_tokens,
               mode="json" if response_format else "text")

            return (response.choices[0].message.content or "").strip()

        except Exception as e:
            if attempt < max_retries - 1:
                ev(Ev.LLM_RETRY, trace_id=trace_id, model=model, client=client_name,
                   attempt=attempt + 1, retry_delay=retry_delay, error=str(e))
                time.sleep(retry_delay)
                continue
            ev(Ev.LLM_FAILED, trace_id=trace_id, model=model, client=client_name,
               attempts=max_retries, error=str(e), exc_info=True)
            raise


__all__ = ["call_json", "call_text", "call_with_template"]
