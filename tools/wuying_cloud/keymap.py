"""
云手机（Android）按键常量 + hotkey → key code 映射。
"""

from __future__ import annotations

KEY_HOME = 3
KEY_BACK = 4
KEY_VOLUME_UP = 24
KEY_VOLUME_DOWN = 25
KEY_POWER = 26
KEY_MENU = 82

HOTKEY_TO_ANDROID: dict[str, int] = {
    "esc": KEY_BACK,
    "escape": KEY_BACK,
    "back": KEY_BACK,
    "home": KEY_HOME,
    "menu": KEY_MENU,
    # 硬件键：阿里云 wuying SDK 的 send_key 支持，值取自 AOSP KeyEvent
    "volume_up": KEY_VOLUME_UP,
    "volumeup": KEY_VOLUME_UP,
    "volume_down": KEY_VOLUME_DOWN,
    "volumedown": KEY_VOLUME_DOWN,
    "power": KEY_POWER,
}

# 故意不映射：enter / tab / backspace / delete / arrows / space / shift / ctrl / command。
# 阿里云 send_key 只接受 HOME/BACK/VOLUME_UP/VOLUME_DOWN/POWER/MENU 共 6 个 keycode，
# 其他值 SDK 会直接拒收。屏幕键盘上的 Enter/退格等请让 LLM 视觉点击完成。

SCROLL_PIXELS_PER_CLICK = 120
SCROLL_MAX_PIXELS = 1600
SCROLL_DURATION_MS = 300
DRAG_DURATION_MS = 500
DBLCLICK_INTERVAL_MS = 80


__all__ = [
    "KEY_HOME",
    "KEY_BACK",
    "KEY_VOLUME_UP",
    "KEY_VOLUME_DOWN",
    "KEY_POWER",
    "KEY_MENU",
    "HOTKEY_TO_ANDROID",
    "SCROLL_PIXELS_PER_CLICK",
    "SCROLL_MAX_PIXELS",
    "SCROLL_DURATION_MS",
    "DRAG_DURATION_MS",
    "DBLCLICK_INTERVAL_MS",
]
