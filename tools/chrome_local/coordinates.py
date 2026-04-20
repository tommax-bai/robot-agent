"""
坐标系：LLM 归一化 0-1000 → 屏幕逻辑像素。

关联的当前 Chrome 窗口位置/尺寸由 `Window` 实例持有，每次 capture 时更新。
以前是 `tools/screen.py` 里的模块级全局 dict——现在是实例字段，多 worker/多账号
不会串。`ChromeLocalEnv` 持有一个 `Window` 实例并共享给 capture/actions。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config


@dataclass
class Window:
    """当前 Chrome 窗口的物理位置和尺寸。capture 写，actions 读。"""

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def update(self, x: int, y: int, w: int, h: int) -> None:
        self.x, self.y, self.w, self.h = x, y, w, h

    def llm_to_screen(self, nx: float, ny: float) -> tuple[int, int]:
        """归一化坐标 (0-1000) → 屏幕像素。窗口未初始化时退回到全屏。"""
        ss = config.settings.system.screen_size
        screen_w = self.w or ss.width
        screen_h = self.h or ss.height
        px = nx * (screen_w / 1000) + self.x
        py = ny * (screen_h / 1000) + self.y
        return int(round(px)), int(round(py))


__all__ = ["Window"]
