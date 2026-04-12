"""
共享 LLM 调用模块：统一封装 LLM 请求、重试逻辑和 Token 记录。
所有 agent（operator、planner、strategist）统一使用此模块。
"""

from __future__ import annotations

import time

import config
import utils.llm_client as llm_client
import utils.logger as logger
from utils.json_utils import extract_json
from utils.token_logger import log_token_usage


def call_llm(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    **kwargs,
) -> tuple[dict, str]:
    """
    统一 LLM 调用（JSON 模式），包含重试逻辑和 Token 记录。
    返回 (parsed_json, raw_text)
    """
    raw = _call_llm_raw(
        messages,
        model,
        client_name,
        trace_id,
        response_format={"type": "json_object"},
        **kwargs,
    )
    return extract_json(raw), raw


def call_llm_text(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    **kwargs,
) -> str:
    """
    统一 LLM 调用（纯文本模式），包含重试逻辑和 Token 记录。
    返回纯文本字符串。
    """
    return _call_llm_raw(messages, model, client_name, trace_id, **kwargs)


def call_llm_with_template(meta: dict, prompt: str, default_temperature: float = 0.9) -> str:
    """
    根据模板 meta 信息和 prompt 内容调用 LLM（纯文本模式），返回文本结果。
    meta 中可指定 provider、model、temperature 及其他透传参数。
    """
    planner_config = config.agent["planner"]
    client_name = meta.get("provider", planner_config["llm_client"])
    model_name = meta.get("model", planner_config["model"])
    temperature = meta.get("temperature", default_temperature)

    extra_params = {k: v for k, v in meta.items() if k not in ("provider", "model", "temperature")}

    return call_llm_text(
        messages=[{"role": "user", "content": prompt}],
        model=model_name,
        client_name=client_name,
        temperature=temperature,
        **extra_params,
    )


def _call_llm_raw(
    messages: list[dict],
    model: str,
    client_name: str,
    trace_id: str = "system",
    response_format: dict | None = None,
    **kwargs,
) -> str:
    """
    底层 LLM 调用：重试 + Token 记录，返回原始文本。
    """
    max_retries = 3
    retry_delay = 2

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

            usage = response.usage
            if usage:
                log_token_usage(
                    trace_id=trace_id,
                    model=model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )

            return (response.choices[0].message.content or "").strip()

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    {
                        "msg": f"LLM 调用失败，正在进行第 {attempt + 1} 次重试",
                        "error": str(e),
                        "retry_delay": retry_delay,
                    },
                    trace_id,
                )
                time.sleep(retry_delay)
                continue

            logger.error({"msg": "LLM 调用多次重试后依然失败", "error": str(e)}, trace_id)
            raise
