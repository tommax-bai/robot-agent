"""
启动时获取屏幕尺寸 + Retina 缩放比例。

设计：
- 优先用 macOS 原生 API（Quartz）查 backing scale factor，无副作用
- fallback 到 pyautogui 截屏方案（会触发系统截屏权限）
"""

from __future__ import annotations

import pyautogui

import config
import utils.logger as logger


def init_screen_size():
    screen_width, screen_height = pyautogui.size()
    scale = _detect_scale(screen_width)

    config.system["screen_size"] = {
        "width": screen_width,
        "height": screen_height,
        "scale": scale,
    }
    logger.sys(f"屏幕尺寸: {screen_width}x{screen_height}, 缩放比例: {scale}")


def _detect_scale(logical_width: int) -> float:
    """
    返回 Retina 缩放比例（physical / logical）。
    macOS 优先用 Quartz API，避免主动截屏。
    """
    try:
        from Quartz import CGDisplayPixelsWide, CGMainDisplayID

        display_id = CGMainDisplayID()
        physical_width = CGDisplayPixelsWide(display_id)
        if physical_width and logical_width:
            return physical_width / logical_width
    except Exception as e:
        logger.warning({"msg": "Quartz 获取缩放失败，回退到 pyautogui 方案", "error": str(e)})

    # Fallback：用 pyautogui 截屏（会触发权限弹窗）
    try:
        screenshot = pyautogui.screenshot()
        return screenshot.width / logical_width if logical_width else 1.0
    except Exception:
        return 1.0
