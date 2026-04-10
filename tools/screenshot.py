"""
截图工具模块

提供屏幕截图功能，支持：
- 截取当前屏幕并返回 base64 编码
- 可选在截图上标记鼠标位置（红色十字）
- 可选持久化保存到本地
- 支持 Retina 屏幕缩放适配
"""

import utils.logger as logger

import os
import time
import config
import base64
import argparse
from io import BytesIO
from typing import Optional

import pyautogui
import pywinctl

from PIL import ImageDraw, Image
from PIL import ImageEnhance
from PIL import ImageOps
from PIL import ImageGrab


def get_screenshot_base64(trace_id: str, include_cursor: bool = False, shot_window: str = "小红书|Chrome") -> tuple[str, int, int]:
    """
    截取当前屏幕并返回 base64 编码的图像数据
    
    Args:
        trace_id: 追踪 ID，用于日志记录和文件保存路径
        include_cursor: 是否在截图上标记鼠标位置（红色十字）
    
    Returns:
        tuple: (base64 编码的图像, 鼠标 x 坐标, 鼠标 y 坐标)
               如果 include_cursor=False，坐标返回 0
    """

    # 初始化鼠标坐标（用于返回值）
    mouse_x_scaled = 0
    mouse_y_scaled = 0

    for title in shot_window.split("|"): 
        windows = pywinctl.getWindowsWithTitle(title, pywinctl.Re.CONTAINS)
        if windows: 
            break

    if not windows:
        return "", 0, 0
    
    window = windows[0]
    window.activate()

    # 截取屏幕
    #screen_img = pyautogui.screenshot()
    screen_img = ImageGrab.grab(bbox=(window.left, window.top, window.right, window.bottom))

    # 图片质量压缩，提升LLM处理效率
    screen_img = screen_img.convert("P", palette=Image.ADAPTIVE, colors=128)
    screen_img = screen_img.convert("RGB")

    enhancer = ImageEnhance.Color(screen_img)
    screen_img = enhancer.enhance(0.7)

    screen_img = ImageOps.posterize(screen_img, bits=5)

    # 如果需要标记鼠标位置，在截图上画红色十字
    if include_cursor:
       mouse_x_scaled, mouse_y_scaled = _draw_mouse_cursor(screen_img, window)
    
    # 如果配置了持久化保存，将截图保存到本地
    if config.system["screenshot"]["persist"]:
        save_dir = config.system["screenshot"]["save_dir"]
        full_path = os.path.join(save_dir, trace_id)
        os.makedirs(full_path, exist_ok=True)
        screen_img.save(os.path.join(full_path, f"screenshot_{time.time()}.jpeg"))
    
    # 将图像转换为 base64 编码
    buffered = BytesIO()
    screen_img.save(buffered, format="JPEG", quality=75)
    img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    from tools import screen as screen_tool
    screen_tool.update_window(
        x=window.left,
        y=window.top,
        w=window.right - window.left,
        h=window.bottom - window.top,
    )

    logger.info({
        "msg": f"截图成功, 鼠标位置: {mouse_x_scaled}, {mouse_y_scaled}, 窗口信息 {screen_tool.get_window()}",
        "trace_id": trace_id,
    })

    return img_base64, mouse_x_scaled, mouse_y_scaled


def _draw_mouse_cursor(screen_img: Image, window) -> tuple[int, int] :
    # 获取缩放比例（Retina 屏幕为 2.0，普通屏幕为 1.0）
    scale = config.system["screen_size"]["scale"]
    
    # 获取鼠标逻辑坐标并转换为物理像素坐标
    draw = ImageDraw.Draw(screen_img)
    mouse_x, mouse_y = pyautogui.position()
    mouse_x_scaled = int(mouse_x * scale) - window.left
    mouse_y_scaled = int(mouse_y * scale) - window.top

    # 十字标记样式配置
    cross_color = "red"
    cross_width = int(4 * scale)    # 线宽随缩放比例调整
    cross_length = int(20 * scale)  # 线长随缩放比例调整
    
    # 绘制水平线
    draw.line(
        [(mouse_x_scaled - cross_length, mouse_y_scaled), 
            (mouse_x_scaled + cross_length, mouse_y_scaled)],
        fill=cross_color, width=cross_width
    )
    # 绘制垂直线
    draw.line(
        [(mouse_x_scaled, mouse_y_scaled - cross_length), 
            (mouse_x_scaled, mouse_y_scaled + cross_length)],
        fill=cross_color, width=cross_width
    )

    return mouse_x, mouse_y

def _debug_main():
    parser = argparse.ArgumentParser(description="Capture a screenshot for debugging")
    parser.add_argument("--trace-id", default="debug", help="trace id used for logging and persistence")
    parser.add_argument("--cursor", action="store_true", help="draw cursor crosshair on screenshot")
    parser.add_argument("--out", default="debug_screenshot.jpg", help="output image path")
    parser.add_argument("--dump-base64", default="", help="optional file path to dump raw base64 text")
    args = parser.parse_args()

    img_base64, mouse_x, mouse_y = get_screenshot_base64(
        trace_id=args.trace_id,
        include_cursor=args.cursor,
    )

    with open(args.out, "wb") as f:
        f.write(base64.b64decode(img_base64))

    if args.dump_base64:
        with open(args.dump_base64, "w", encoding="utf-8") as f:
            f.write(img_base64)

    print(f"saved_image: {args.out}")
    print(f"base64_length: {len(img_base64)}")
    print(f"cursor: ({mouse_x}, {mouse_y})")
    if args.dump_base64:
        print(f"saved_base64: {args.dump_base64}")


if __name__ == "__main__":
    _debug_main()
