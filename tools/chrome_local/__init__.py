"""
本地 macOS Chrome 执行环境。

依赖：
- PyAutoGUI / pyperclip（鼠标键盘）
- Pillow ImageGrab + pywinctl（窗口截屏）
- utils.init_functions.init_chrome_client（多开窗口定位）
需要 macOS 辅助功能权限。
"""

from __future__ import annotations

import config
from tools.chrome_local import actions as _actions
from tools.chrome_local import capture as _capture
from tools.chrome_local.cleanup import cleanup_chrome_environment
from tools.chrome_local.coordinates import Window


class ChromeLocalEnv:
    """本地 macOS Chrome 执行环境。实现 `tools.environment.Environment` 协议。

    `Window` 作为实例字段替代以前 `tools/screen.py` 的模块级全局状态——
    capture 时写入窗口尺寸，actions 时读出做坐标转换。
    """

    def __init__(self) -> None:
        self._window = Window()
        # content_type 取自 config 里 chromelocal profile，随 profile.format 变
        fmt = config.settings.system.screenshot.chromelocal.format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        self.content_type = f"image/{fmt}"

    def capture(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        return _capture.capture(self._window, trace_id, include_cursor=include_cursor)

    def perform(self, trace_id: str, action: dict) -> dict:
        return _actions.dispatch(self._window, trace_id, action)


__all__ = ["ChromeLocalEnv", "cleanup_chrome_environment"]
