"""
ModeState: Agent 模式枚举与切换。

把 AgentMode 的存储 + 过渡事件从 Supervisor 中拎出来。ModeState 只负责
"当前是什么模式 / 切到什么模式 / 切换时广播 EVT_MODE_CHANGED"；
切换引发的副作用（比如取消当前运行）由 Supervisor 显式完成，不在这里偷偷做。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

import utils.logger as logger

if TYPE_CHECKING:
    from runtime.events import EventBus


class AgentMode(Enum):
    WAITING = "waiting"
    PATROLLING = "patrolling"
    EXECUTING = "executing"
    DEBUG = "debug"


class ModeState:
    def __init__(self, initial: AgentMode, event_bus: EventBus | None, account_id: str = "default"):
        self._mode = initial
        self._events = event_bus
        self._account_id = account_id

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def transition_to(self, new: AgentMode) -> tuple[AgentMode, AgentMode]:
        """切换模式，返回 (old, new)。发布 EVT_MODE_CHANGED。"""
        old = self._mode
        self._mode = new
        logger.info({"msg": "Agent 模式切换", "account": self._account_id, "old": old.value, "new": new.value})
        if self._events is not None:
            from runtime.events import Event, payload

            self._events.publish(Event.MODE_CHANGED, payload(old=old.value, new=new.value))
        return old, new

    def is_idle(self) -> bool:
        return self._mode in (AgentMode.DEBUG, AgentMode.WAITING)

    def is_executing(self) -> bool:
        return self._mode == AgentMode.EXECUTING

    def is_patrolling(self) -> bool:
        return self._mode == AgentMode.PATROLLING
