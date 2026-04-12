from __future__ import annotations

from functools import lru_cache
from openai import OpenAI
import config

@lru_cache(maxsize=config.model["lru_cache_max_size"])
def get_client(client_name: str) -> OpenAI:
    client_config = config.model["clients"].get(client_name, None)
    if not client_config:
        raise ValueError(f"没有找到名为 {client_name} 的 LLM 配置，请在 config.py 中配置")
    return OpenAI(
        api_key=client_config["api_key"],
        base_url=client_config["base_url"],
        timeout=200.0)
