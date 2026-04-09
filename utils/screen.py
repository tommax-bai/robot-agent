import config
from runtime import context
from utils import logger


def llm_to_screen(x: float, y: float) -> tuple[int, int]:
    """
    将归一化坐标 (0-1000) 转换为屏幕逻辑像素坐标
    
    Args:
        x: 归一化 x 坐标 (0-1000)
        y: 归一化 y 坐标 (0-1000)
    
    Returns:
        tuple: (屏幕 x 坐标, 屏幕 y 坐标)
    """
    screen_w = context.core["window"]["w"] or config.system["screen_size"]["width"]
    screen_h = context.core["window"]["h"] or config.system["screen_size"]["height"]

    pixel_x = x * (screen_w / 1000)
    pixel_y = y * (screen_h / 1000)

    # pixel_x, pixel_y = x, y

    pixel_x += context.core["window"]["x"]
    pixel_y += context.core["window"]["y"]

    logger.info(f"屏幕尺寸: {screen_w}x{screen_h}")
    logger.info(f"窗口位置: ({context.core['window']['x']}, {context.core['window']['y']}, {context.core['window']['w']}, {context.core['window']['h']})")
    logger.info(f"坐标转换: ({x}, {y}) -> ({pixel_x}, {pixel_y})")

    return int(round(pixel_x)), int(round(pixel_y))