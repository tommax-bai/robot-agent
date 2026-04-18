"""
StatusSnapshot: Supervisor.status() 返回的不可变状态快照。

原来 get_status() 直接返回 dict，API 层想引用字段只能靠字符串键，没有类型检查。
改成 dataclass 之后：代码内用字段访问，JSON API 通过 as_dict() 序列化。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StatusSnapshot:
    mode: str
    is_active: bool
    task_kind: str
    current_slot: str
    current_trace_id: str | None
    current_goal: str | None
    running_seconds: int
    timestamp: str

    def as_dict(self) -> dict:
        return asdict(self)
