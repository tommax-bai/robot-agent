"""OpenAI 兼容客户端工厂：按 client 名在 settings.llm.clients 里查配置并缓存。"""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

import config


@lru_cache(maxsize=config.settings.llm.lru_cache_max_size)
def get_client(client_name: str) -> OpenAI:
    client_config = config.settings.llm.clients.get(client_name)
    if not client_config:
        raise ValueError(f"没有找到名为 {client_name} 的 LLM 配置，请在 config 中配置")

    if not client_config.api_key:
        raise ValueError(
            f"LLM 客户端 {client_name} 缺少 api_key，请设置环境变量 {client_config.env_var}"
        )

    return OpenAI(
        api_key=client_config.api_key,
        base_url=client_config.base_url,
        timeout=200.0,
    )
