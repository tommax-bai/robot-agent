"""无影云手机/云桌面拟人化基础原语。

设计原则：
  - 全部由 config.settings.system.input 控制开关，默认关闭 → 行为等价旧实现
  - 用 time.sleep（同步）：env.perform 通过 asyncio.to_thread 在线程池执行，
    handler 自身是同步的，没必要装 async
  - SDK 的 swipe/drag 是 server-side 原子调用，无法在 client 侧插路点 → 这里只能补
    "前后停顿"和"坐标抖动"两件事；逐字符输入靠循环调用 input_text 实现
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable

import config


def _input_cfg():
    return config.settings.system.input


def jitter_xy(x: int, y: int, radius: int) -> tuple[int, int]:
    """坐标 ±radius 像素抖动；radius<=0 直接返回原坐标。"""
    if radius <= 0:
        return x, y
    return x + random.randint(-radius, radius), y + random.randint(-radius, radius)


def maybe_pre_pause() -> None:
    """动作前的微停顿（0.02~0.08s），按 system.input.pre_action_pause 开关。"""
    if _input_cfg().pre_action_pause:
        time.sleep(random.uniform(0.02, 0.08))


def maybe_post_pause() -> None:
    """动作后的微停顿（0.05~0.15s），按 system.input.pre_action_pause 开关。"""
    if _input_cfg().pre_action_pause:
        time.sleep(random.uniform(0.05, 0.15))


def type_chunked(
    input_text_fn: Callable[[str], Any],
    text: str,
    *,
    min_ms: int,
    max_ms: int,
) -> tuple[bool, str, int]:
    """逐字符调用 SDK 的 input_text，每字之间按 [min_ms, max_ms] 均匀抖动。

    返回 (overall_ok, first_error_message, chars_sent)。
    任意一个字符失败立即停止——继续灌入只会让现场状态更难诊断。
    """
    sent = 0
    for ch in text:
        result = input_text_fn(ch)
        ok = bool(getattr(result, "success", False))
        if not ok:
            err = str(getattr(result, "error_message", "") or "input_text 单字符失败")
            return False, err, sent
        sent += 1
        delay = random.uniform(min_ms, max_ms) / 1000.0
        time.sleep(delay)
    return True, "", sent


__all__ = [
    "jitter_xy",
    "maybe_pre_pause",
    "maybe_post_pause",
    "type_chunked",
]
