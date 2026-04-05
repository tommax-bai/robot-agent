import pyautogui
import config
import utils.logger as logger


def init_screen_size():
    screen_width, screen_height = pyautogui.size()

    screenshot = pyautogui.screenshot()
    scale = screenshot.width / screen_width
    
    config.global_config["screen_size"] = {
        "width": screen_width,
        "height": screen_height,
        "scale": scale,
    }
    logger.sys(f"屏幕尺寸: {screen_width}x{screen_height}, 缩放比例: {scale}")