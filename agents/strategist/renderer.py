"""PromptRenderer：模板预加载 + 渲染 + LLM 调用 的管道。

替代原 Strategist 内的 `_render_and_call`：模板在构造期一次性加载，
而不是每次调用都重读磁盘。
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import StrategistError
from utils.prompt import load_prompt

if TYPE_CHECKING:
    from tools.llm import LlmTool


class PromptRenderer:
    def __init__(self, llm: LlmTool, prompts_dir: str = "prompts/agent"):
        self._llm = llm
        self._prompts_dir = prompts_dir
        self._cache: dict[str, tuple[dict, str]] = {}

    def preload(self, *template_names: str) -> None:
        """可选：启动时一次性加载指定模板。未调用也无碍，首次 render 时会懒加载。"""
        for name in template_names:
            self._load(name)

    async def render_and_call(
        self,
        template: str,
        *,
        default_temperature: float,
        trace_id: str,
        **vars,
    ) -> str:
        """渲染模板 → 调 LLM → 返回文本。失败抛 StrategistError。

        LLM 调用走 asyncio.to_thread，避免阻塞调度 loop —— 所有 Strategist 调用点
        都在 async 路径上，底下的 call_with_template 是同步的 httpx 请求。
        """
        try:
            meta, body = self._load(template)
            prompt = body.format(**vars)
            return await asyncio.to_thread(
                self._llm.call_with_template,
                meta,
                prompt,
                default_temperature=default_temperature,
            )
        except StrategistError:
            raise
        except Exception as e:
            logger.error(
                {"msg": "prompt 模板调用失败", "template": template, "error": str(e)},
                trace_id,
            )
            raise StrategistError(f"render_and_call({template}) failed: {e}") from e

    def _load(self, name: str) -> tuple[dict, str]:
        if name not in self._cache:
            self._cache[name] = load_prompt(os.path.join(self._prompts_dir, name))
        return self._cache[name]
