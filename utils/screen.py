from config import global_config


def normalize_to_screen(x: float, y: float, is_window: bool = False) -> tuple[int, int]:
    """
    将归一化坐标 (0-1000) 转换为屏幕逻辑像素坐标
    
    Args:
        x: 归一化 x 坐标 (0-1000)
        y: 归一化 y 坐标 (0-1000)
    
    Returns:
        tuple: (屏幕 x 坐标, 屏幕 y 坐标)
    """
    screen_width = global_config["screen_size"]["width"]
    screen_height = global_config["screen_size"]["height"]
    pixel_x = (x / 1000) * screen_width
    pixel_y = (y / 1000) * screen_height
    # pixel_x = x
    # pixel_y = y
    return int(round(pixel_x)), int(round(pixel_y))

def window_to_screen():
    pass