"""
Environment 协议：Agent 与执行环境（本地 Chrome / 云手机 / 远程 HTTP）之间的边界。

一个 Environment 只做两件事：
- capture: 采集当前环境视觉（返回 base64 图像 + 光标坐标）
- perform: 执行一条原子动作（click/scroll/paste/...）

动作参数仍为 dict（来自 LLM 输出），但各实现应调用 `coerce_param` 做脏 key 容错，
不再各自实现一份 `get_param` / `_get`。
"""

from __future__ import annotations

from typing import Any, Protocol


class Environment(Protocol):
    """执行环境协议。

    实现类负责把 agent 层的"看/做"请求翻译到具体设备：
    - capture(trace_id, include_cursor) -> (base64 图像, cursor_x, cursor_y)
    - perform(trace_id, action) -> {ok, message, finish, ...}
    """

    def capture(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        ...

    def perform(self, trace_id: str, action: dict) -> dict:
        ...


def coerce_param(params: dict, key: str, default: Any = None) -> Any:
    """LLM 输出参数 dict 的容错取值。

    现实里 LLM 经常产出 "X" / 'x' / \\"x\\" / " x " 等脏 key，这里统一兜底。
    放到 environment 层是为了避免 macos_chrome / cloud_mobile 各自写一份。
    """
    if not isinstance(params, dict):
        return default

    if key in params:
        return params[key]

    for variant in (key.lower(), key.upper(), f'"{key}"', f"'{key}'", f'\\"{key}\\"', f" {key} "):
        if variant in params:
            return params[variant]

    target = key.lower()
    for k in params.keys():
        clean = str(k).strip().strip('"').strip("'").strip('\\"').lower()
        if clean == target:
            return params[k]

    return default


__all__ = ["Environment", "coerce_param"]
