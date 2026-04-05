from functools import lru_cache
from config import global_config
from openai import OpenAI
import config

@lru_cache(maxsize=config.global_config["llm"]["lru_cache_max_size"])
def get_client(client_name: str) -> OpenAI:
    client_config = global_config["llm"]["clients"].get(client_name, None)
    if not client_config:
        raise ValueError(f"没有找到名为 {client_name} 的 LLM 配置，请在 config.py 中配置")
    return OpenAI(
        api_key=client_config["api_key"],
        base_url=client_config["base_url"],
        timeout=200.0)
