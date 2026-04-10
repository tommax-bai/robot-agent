"""
屏幕坐标工具：维护当前 Chrome 窗口的位置/尺寸，提供 LLM 归一化坐标到物理像素的转换。

window 状态是 screen tool 的内部状态：
- screenshot 工具调用 update_window() 写入
- llm_to_screen 读取做坐标转换
不再放在 runtime/context.py 中混淆 RunContext 的角色。
"""
import config
from utils import logger


# 模块私有：当前 Chrome 窗口的物理位置和尺寸（由 screenshot 工具更新）
_window: dict[str, int] = {"x": 0, "y": 0, "w": 0, "h": 0}


def update_window(x: int, y: int, w: int, h: int) -> None:
    """由 screenshot 工具在每次截屏时调用，更新当前窗口的物理位置和尺寸"""
    _window["x"] = x
    _window["y"] = y
    _window["w"] = w
    _window["h"] = h


def get_window() -> dict[str, int]:
    """读取当前窗口的物理位置和尺寸（拷贝，避免外部修改）"""
    return dict(_window)


def llm_to_screen(x: float, y: float) -> tuple[int, int]:
    """
    将归一化坐标 (0-1000) 转换为屏幕逻辑像素坐标

    Args:
        x: 归一化 x 坐标 (0-1000)
        y: 归一化 y 坐标 (0-1000)

    Returns:
        tuple: (屏幕 x 坐标, 屏幕 y 坐标)
    """
    screen_w = _window["w"] or config.system["screen_size"]["width"]
    screen_h = _window["h"] or config.system["screen_size"]["height"]

    pixel_x = x * (screen_w / 1000) + _window["x"]
    pixel_y = y * (screen_h / 1000) + _window["y"]

    logger.info(f"屏幕尺寸: {screen_w}x{screen_h}")
    logger.info(f"窗口位置: ({_window['x']}, {_window['y']}, {_window['w']}, {_window['h']})")
    logger.info(f"坐标转换: ({x}, {y}) -> ({pixel_x}, {pixel_y})")

    return int(round(pixel_x)), int(round(pixel_y))
