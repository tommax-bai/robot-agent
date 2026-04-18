"""平台探测：OS 类型 + 屏幕尺寸与 DPI 缩放。

- macOS：Quartz API 读 backing scale factor（无副作用、无权限弹窗）
- Windows：Win32 API (shcore) 读系统 DPI
- 最终 fallback：pyautogui 截屏（macOS 会触发权限弹窗）
"""

from __future__ import annotations

import sys

import pyautogui

import config
import utils.logger as logger


def detect_platform() -> None:
    system_info = sys.platform
    config.settings.system.system_info = system_info
    logger.sys(f"系统信息: {system_info}")
    if system_info not in ("win32", "darwin"):
        raise ValueError(f"系统信息: {system_info} 不支持")


def detect_screen() -> None:
    width, height = pyautogui.size()
    scale = _detect_scale(width)
    ss = config.settings.system.screen_size
    ss.width, ss.height, ss.scale = width, height, scale
    logger.sys(f"屏幕尺寸: {width}x{height}, 缩放比例: {scale}")


def _detect_scale(logical_width: int) -> float:
    if sys.platform == "darwin":
        try:
            from Quartz import CGDisplayPixelsWide, CGMainDisplayID

            physical_width = CGDisplayPixelsWide(CGMainDisplayID())
            if physical_width and logical_width:
                return physical_width / logical_width
        except Exception as e:
            logger.warning({"msg": "Quartz 获取缩放失败，回退到 pyautogui 方案", "error": str(e)})
    elif sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.GetScaleFactorForDevice.restype = ctypes.c_int
            scale_pct = ctypes.windll.shcore.GetScaleFactorForDevice(0)
            if scale_pct > 0:
                return scale_pct / 100.0
        except Exception as e:
            logger.warning({"msg": "shcore 获取缩放失败，回退到 pyautogui 方案", "error": str(e)})

    try:
        screenshot = pyautogui.screenshot()
        return screenshot.width / logical_width if logical_width else 1.0
    except Exception:
        return 1.0
