"""
本地 macOS Chrome 后端。

把现有的 tools.actions（PyAutoGUI 拟人化操作）和 tools.screenshot（窗口定位 + ImageGrab）
包装成 ActionBackend 接口，行为与重构前完全一致。
"""

from __future__ import annotations

import tools.actions as _actions
import tools.screenshot as _screenshot


class MacOSChromeBackend:
    """本地 macOS Chrome 执行后端。

    依赖：
    - PyAutoGUI / pyperclip（鼠标键盘）
    - Pillow ImageGrab + pywinctl（窗口截屏）
    - utils.init_functions.init_chrome_client（多开窗口定位）
    需要 macOS 辅助功能权限。
    """

    def screenshot(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        return _screenshot.get_screenshot_base64(trace_id, include_cursor=include_cursor)

    def execute_action(self, trace_id: str, action: dict) -> dict:
        return _actions.execute_action(trace_id, action)
