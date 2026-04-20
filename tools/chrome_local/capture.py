"""
本地 Chrome 窗口截图：CDP 对齐选窗 → ImageGrab 截屏 → 压缩 profile → base64。
"""

from __future__ import annotations

import base64
import os
import time

import pyautogui
import pywinctl
from PIL import Image, ImageDraw, ImageGrab

import config
import utils.logger as logger
from tools.image_postprocess import encode_for_llm
from tools.chrome_local.coordinates import Window


def capture(
    window: Window,
    trace_id: str,
    include_cursor: bool = False,
    shot_window: str = "小红书|Chrome",
) -> tuple[str, int, int]:
    """截屏并回写 `window` 尺寸。返回 (base64, cursor_x, cursor_y)。"""
    mouse_x_scaled = 0
    mouse_y_scaled = 0

    candidates: list = []
    for title in shot_window.split("|"):
        candidates.extend(pywinctl.getWindowsWithTitle(title, pywinctl.Re.CONTAINS))

    if not candidates:
        return "", 0, 0

    win = _pick_window(candidates, trace_id)
    win.activate()

    screen_img = ImageGrab.grab(bbox=(win.left, win.top, win.right, win.bottom))

    if include_cursor:
        mouse_x_scaled, mouse_y_scaled = _draw_mouse_cursor(screen_img, win)

    # 统一走 image_postprocess.encode_for_llm —— chromelocal profile 默认激进压缩
    ss = config.settings.system.screenshot
    data, _content_type = encode_for_llm(screen_img, ss.chromelocal)

    if ss.persist:
        save_dir = ss.save_dir
        full_path = os.path.join(save_dir, trace_id)
        os.makedirs(full_path, exist_ok=True)
        # 落盘用编码后的字节，和发给 LLM 的完全一致，便于复现问题
        ext = ss.chromelocal.format.lower()
        if ext == "jpg":
            ext = "jpeg"
        with open(os.path.join(full_path, f"screenshot_{time.time()}.{ext}"), "wb") as f:
            f.write(data)

    b64 = base64.b64encode(data).decode("utf-8")

    window.update(
        x=win.left,
        y=win.top,
        w=win.right - win.left,
        h=win.bottom - win.top,
    )

    logger.debug(
        {"msg": f"截图成功, 鼠标位置: {mouse_x_scaled}, {mouse_y_scaled}", "trace_id": trace_id}
    )
    return b64, mouse_x_scaled, mouse_y_scaled


def _pick_window(candidates: list, trace_id: str):
    """多开 Chrome 时仅靠 title 会选错，用 CDP bounds 对齐到本项目自己的窗口。"""
    target_bounds = None
    try:
        from bootstrap.chrome_client import get as get_chrome

        target_bounds = get_chrome().get_window_bounds()
    except Exception:
        target_bounds = None

    if not target_bounds:
        return candidates[0]

    tx = target_bounds["left"]
    ty = target_bounds["top"]
    best = None
    best_dist = None
    for w in candidates:
        try:
            d = abs(w.left - tx) + abs(w.top - ty)
        except Exception:
            continue
        if best is None or d < best_dist:
            best = w
            best_dist = d

    if best is not None and best_dist is not None and best_dist <= 20:
        return best

    logger.warning(
        {
            "msg": "未能通过 CDP bounds 定位本项目 Chrome 窗口，回退到标题首匹配",
            "target_bounds": target_bounds,
            "candidates": [(w.title, w.left, w.top) for w in candidates],
            "trace_id": trace_id,
        }
    )
    return candidates[0]


def _draw_mouse_cursor(screen_img: Image.Image, window) -> tuple[int, int]:
    scale = config.settings.system.screen_size.scale
    draw = ImageDraw.Draw(screen_img)
    mouse_x, mouse_y = pyautogui.position()
    mx = int(mouse_x * scale) - window.left
    my = int(mouse_y * scale) - window.top

    color = "red"
    width = int(4 * scale)
    length = int(20 * scale)

    draw.line([(mx - length, my), (mx + length, my)], fill=color, width=width)
    draw.line([(mx, my - length), (mx, my + length)], fill=color, width=width)
    return mouse_x, mouse_y


__all__ = ["capture"]
