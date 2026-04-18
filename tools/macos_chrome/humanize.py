"""
拟人化基础原语：贝塞尔鼠标轨迹、微抖动、动作前后的短暂停顿。

这些不是"动作"，是"动作风格"。分离出来以便：
- 测试时可以 monkey-patch 为瞬时实现
- 未来想换不同拟人策略时不需要改 actions 逻辑
"""

from __future__ import annotations

import math
import random
import time

import pyautogui


def move(x: float, y: float) -> None:
    """贝塞尔曲线 + ease-in-out 的拟人鼠标移动。短距离快移，长距离分段。"""
    cx, cy = pyautogui.position()
    dist = math.hypot(x - cx, y - cy)

    if dist < 5:
        pyautogui.moveTo(x, y)
        return

    duration = min(0.15 + dist / 3000, 0.5)
    mid_x = (cx + x) / 2 + random.uniform(-dist * 0.05, dist * 0.05)
    mid_y = (cy + y) / 2 + random.uniform(-dist * 0.05, dist * 0.05)

    steps = max(int(dist / 75), 6)
    for i in range(1, steps + 1):
        t = i / steps
        t_ease = t * t * (3 - 2 * t)
        bx = (1 - t_ease) ** 2 * cx + 2 * (1 - t_ease) * t_ease * mid_x + t_ease**2 * x
        by = (1 - t_ease) ** 2 * cy + 2 * (1 - t_ease) * t_ease * mid_y + t_ease**2 * y
        pyautogui.moveTo(bx, by)
        time.sleep(duration / steps)

    pyautogui.moveTo(x, y)


def jitter(radius: int = 2) -> tuple[int, int]:
    """微小随机偏移，模拟人手不可能完美命中像素中心。"""
    return random.randint(-radius, radius), random.randint(-radius, radius)


def pre_action_pause() -> None:
    """动作前的微停顿：点击前的极短犹豫。"""
    time.sleep(random.uniform(0.02, 0.08))


def post_action_pause() -> None:
    """动作后的微停顿：点击后的短暂观察。"""
    time.sleep(random.uniform(0.05, 0.15))


def drag(x1: float, y1: float, x2: float, y2: float) -> None:
    """三段式拟人拖拽：慢启动（0-15%）→ 冲刺（15-85%）→ 精准减速（85-100%）。"""
    dist_x = x2 - x1
    dist_y = y2 - y1

    move(x1, y1)
    time.sleep(random.uniform(0.1, 0.2))
    pyautogui.mouseDown()

    def _j() -> int:
        return random.randint(-1, 0)

    for p in range(0, 15, 1):
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio) + _j()
        cur_y = y1 + (dist_y * ratio) + _j()
        cur_x = min(cur_x, x1 + dist_x * 0.15)
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)
        if random.random() < 0.4:
            time.sleep(0.002)

    p = 15
    while p < 85:
        step = random.randint(4, 8)
        step = min(step, 85 - p)
        p += step
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio)
        cur_y = y1 + (dist_y * ratio)
        pyautogui.moveTo(cur_x, cur_y, duration=0.0005)
        if random.random() < 0.05:
            time.sleep(0.001)

    for p in range(85, 101, 1):
        fade_factor = (100 - p) / 15.0
        jitter_y = 0
        if random.random() < fade_factor:
            jitter_y = random.randint(-1, 1)
        ratio = p / 100.0
        cur_x = x1 + (dist_x * ratio)
        cur_y = y1 + (dist_y * ratio) + jitter_y
        pyautogui.moveTo(cur_x, cur_y, duration=0.001)
        if random.random() < 0.5 * fade_factor:
            time.sleep(0.003)

    pyautogui.moveTo(x2, y2, duration=0.1)
    time.sleep(random.uniform(0.1, 0.2))
    pyautogui.mouseUp()


__all__ = ["move", "jitter", "pre_action_pause", "post_action_pause", "drag"]
