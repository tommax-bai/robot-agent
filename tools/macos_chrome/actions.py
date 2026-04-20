"""
本地 Chrome 原子动作实现：PyAutoGUI + 拟人化原语。

每个 handler 接收 (window, params, finish)，返回 {ok, message, finish} dict。
window 用于 LLM 归一化坐标 → 屏幕像素。
"""

from __future__ import annotations

import json
import random
import time

import pyautogui
import pyperclip

import config
import utils.logger as logger
from tools.environment import coerce_param
from tools.macos_chrome import humanize
from tools.macos_chrome.coordinates import Window

# 禁用 PyAutoGUI FAILSAFE，防止鼠标滑到角落时任务中断
pyautogui.FAILSAFE = False


def dispatch(window: Window, trace_id: str, action: dict) -> dict:
    method = action.get("method")
    params = action.get("params", {}) or {}
    finish = bool(action.get("finish", False))

    logger.info(
        {"msg": "执行器收到动作指令", "method": method, "params_raw": json.dumps(params, ensure_ascii=False)},
        trace_id,
    )

    if not method:
        return {"ok": False, "message": "没有拿到有效的 action", "error": "missing action field", "finish": False}

    handler = _HANDLERS.get(method)
    if handler is None:
        return {"ok": False, "message": f"不支持的动作: {method}", "error": "unsupported action", "finish": finish}

    try:
        return handler(window, params, finish, trace_id)
    except Exception as e:
        logger.error({"msg": f"执行器内部报错: {method}", "error": str(e), "params": params}, trace_id)
        return {"ok": False, "message": f"执行报错: {e}", "error": type(e).__name__, "finish": finish}


# ─── 动作实现 ────────────────────────────────────────────


def _click(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")
    if x_val is None or y_val is None:
        raise KeyError(f"缺少坐标参数 x 或 y, 收到: {list(params.keys())}")

    x, y = window.llm_to_screen(float(x_val), float(y_val))
    jx, jy = humanize.jitter(2)

    logger.debug({"msg": f"human click → ({x}, {y})"})
    humanize.move(x + jx, y + jy)
    humanize.pre_action_pause()
    pyautogui.click()
    humanize.post_action_pause()

    return {"ok": True, "message": f"已尝试点击操作 ({x}, {y})", "finish": finish}


def _dblclick(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")
    x, y = window.llm_to_screen(float(x_val), float(y_val))
    jx, jy = humanize.jitter(2)

    humanize.move(x + jx, y + jy)
    humanize.pre_action_pause()
    interval = random.uniform(0.05, 0.12)
    pyautogui.click()
    time.sleep(interval)
    pyautogui.click()
    humanize.post_action_pause()

    return {"ok": True, "message": f"已尝试双击操作 ({x}, {y})", "finish": finish}


def _move(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")
    x, y = window.llm_to_screen(float(x_val), float(y_val))

    logger.debug({"msg": f"human move → ({x}, {y})"})
    humanize.move(x, y)
    time.sleep(random.uniform(0.1, 0.3))

    return {"ok": True, "message": f"已尝试移动操作 ({x}, {y})", "finish": finish}


def _scroll(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    clicks = int(coerce_param(params, "clicks", 1))
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")

    if x_val is not None and y_val is not None:
        sx, sy = window.llm_to_screen(float(x_val), float(y_val))
    else:
        x1 = coerce_param(params, "x1")
        y1 = coerce_param(params, "y1")
        if x1 is None or y1 is None:
            raise KeyError(f"缺少滚动坐标参数, 收到: {list(params.keys())}")
        sx, sy = window.llm_to_screen(float(x1), float(y1))

    humanize.move(sx, sy)
    time.sleep(random.uniform(0.05, 0.15))

    abs_clicks = min(int(abs(clicks) * 0.5), 200)
    direction = 1 if clicks > 0 else -1

    remaining = abs_clicks
    while remaining > 0:
        step = min(random.randint(30, 50), remaining)
        pyautogui.scroll(step * direction)
        remaining -= step

        r = random.random()
        if r < 0.15:
            time.sleep(random.uniform(0.3, 0.6))
        elif r < 0.55:
            time.sleep(random.uniform(0.1, 0.25))
        else:
            time.sleep(random.uniform(0.04, 0.1))

    return {
        "ok": True,
        "message": f"滚动操作已执行 (方向:{'上' if clicks > 0 else '下'}, 强度:{abs(clicks)})",
        "finish": finish,
    }


def _drag(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    x1 = float(coerce_param(params, "x1"))
    y1 = float(coerce_param(params, "y1"))
    x2 = float(coerce_param(params, "x2"))
    y2 = float(coerce_param(params, "y2"))
    sx1, sy1 = window.llm_to_screen(x1, y1)
    sx2, sy2 = window.llm_to_screen(x2, y2)
    humanize.drag(sx1, sy1, sx2, sy2)
    return {"ok": True, "message": "已尝试拖动操作", "finish": finish}


def _paste(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    text = coerce_param(params, "text", "")
    pyperclip.copy(text)

    humanize.pre_action_pause()
    modifier = "command" if config.settings.system.system_info == "darwin" else "ctrl"
    pyautogui.hotkey(modifier, "v")
    time.sleep(random.uniform(0.1, 0.3))

    return {"ok": True, "message": "已尝试粘贴操作", "finish": finish}


def _copy(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    text = coerce_param(params, "text", "")
    pyperclip.copy(text)
    time.sleep(random.uniform(0.03, 0.08))
    return {"ok": True, "message": "已尝试复制操作", "finish": finish}


def _wait(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    ms = float(coerce_param(params, "milliseconds", 0))
    jitter = ms * random.uniform(-0.1, 0.1)
    actual_ms = max(ms + jitter, 0)
    time.sleep(actual_ms / 1000.0)
    return {"ok": True, "message": f"已尝试等待操作 ({actual_ms:.0f}ms)", "finish": finish}


def _hotkey(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    keys = coerce_param(params, "keys", "")
    key_list = keys.split("+")

    humanize.pre_action_pause()
    for key in key_list:
        pyautogui.keyDown(key.strip())
        time.sleep(random.uniform(0.02, 0.06))
    time.sleep(random.uniform(0.03, 0.08))
    for key in reversed(key_list):
        pyautogui.keyUp(key.strip())
        time.sleep(random.uniform(0.01, 0.04))
    humanize.post_action_pause()

    return {"ok": True, "message": "已尝试快捷键操作", "finish": finish}


def _long_press(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    """鼠标长按。桌面上多用于拖拽的抓取阶段、右键菜单替代。"""
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")
    if x_val is None or y_val is None:
        raise KeyError(f"缺少坐标参数 x 或 y, 收到: {list(params.keys())}")
    ms = int(float(coerce_param(params, "milliseconds", 600) or 600))
    ms = max(ms, 200)

    x, y = window.llm_to_screen(float(x_val), float(y_val))
    humanize.move(x, y)
    humanize.pre_action_pause()
    pyautogui.mouseDown()
    time.sleep(ms / 1000.0)
    pyautogui.mouseUp()
    humanize.post_action_pause()

    return {"ok": True, "message": f"已长按 ({x}, {y}) {ms}ms", "finish": finish}


def _clear_input(window: Window, params: dict, finish: bool, trace_id: str) -> dict:
    """清空输入框：click 获焦 → command+a 全选 → delete 删除。在桌面是完全原子的。"""
    x_val = coerce_param(params, "x")
    y_val = coerce_param(params, "y")
    if x_val is None or y_val is None:
        raise KeyError(f"缺少坐标参数 x 或 y, 收到: {list(params.keys())}")

    x, y = window.llm_to_screen(float(x_val), float(y_val))
    humanize.move(x, y)
    humanize.pre_action_pause()
    pyautogui.click()
    humanize.post_action_pause()
    time.sleep(random.uniform(0.08, 0.15))

    # command+a 全选
    pyautogui.keyDown("command")
    time.sleep(random.uniform(0.03, 0.06))
    pyautogui.press("a")
    time.sleep(random.uniform(0.02, 0.05))
    pyautogui.keyUp("command")
    time.sleep(random.uniform(0.05, 0.10))

    # delete 删除（macOS 的 delete 就是退格；forward-delete 要 fn+delete）
    pyautogui.press("delete")
    humanize.post_action_pause()

    return {"ok": True, "message": f"已清空输入框 ({x}, {y})", "finish": finish}


_HANDLERS = {
    "click": _click,
    "dblclick": _dblclick,
    "move": _move,
    "scroll": _scroll,
    "drag": _drag,
    "paste": _paste,
    "copy": _copy,
    "wait": _wait,
    "hotkey": _hotkey,
    "long_press": _long_press,
    "clear_input": _clear_input,
}


__all__ = ["dispatch"]
