"""
Vision-action 循环的对话历史。每个子任务一份实例，不依赖全局可变状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationHistory:
    """Vision 观察-思考-行动循环的消息缓冲。"""

    max_rounds: int = 6
    system_message: dict[str, Any] = field(default_factory=dict)
    user_messages: list[dict[str, Any]] = field(default_factory=list)
    assistant_messages: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def set_system(self, content: str) -> None:
        self.system_message = {"role": "system", "content": content}

    def set_user(self, content: list[dict[str, Any]]) -> None:
        self.messages = [{"role": "user", "content": content}]
        self.user_messages = list(self.messages)
        self.assistant_messages = []

    def add_user(self, content: list[dict[str, Any]]) -> None:
        self.messages.append({"role": "user", "content": content})
        self._trim()
        self.user_messages = [m for m in self.messages if m.get("role") == "user"]

    def add_assistant(self, raw: str) -> None:
        self.messages.append({"role": "assistant", "content": raw})
        self._trim()
        self.assistant_messages = [m for m in self.messages if m.get("role") == "assistant"]

    def to_messages(self) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if self.system_message:
            msgs.append(self.system_message)
        msgs.extend(self.messages)
        return msgs

    def _trim(self) -> None:
        self.messages = self.messages[-self.max_rounds * 2:]


__all__ = ["ConversationHistory"]
