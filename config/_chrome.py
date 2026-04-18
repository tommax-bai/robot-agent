"""本地 Chrome for Testing 的默认安装路径（按平台）。

约定：用户把解压后的目录放在
  ~/codes/chrome-<platform>/ 和 ~/codes/chromedriver-<platform>/
实际路径不一致时通过 CHROME_BINARY / CHROMEDRIVER_PATH 环境变量覆盖。
"""
from __future__ import annotations

import os
import sys


def default_paths() -> tuple[str, str, str]:
    """返回 (chrome_binary, chromedriver_dir, chromedriver_exe_name)。"""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return (
            os.path.join(home, "codes", "chrome-win64", "chrome.exe"),
            os.path.join(home, "codes", "chromedriver-win64"),
            "chromedriver.exe",
        )
    if sys.platform == "darwin":
        return (
            os.path.join(
                home, "codes", "chrome-mac-arm64",
                "Google Chrome for Testing.app", "Contents",
                "MacOS", "Google Chrome for Testing",
            ),
            os.path.join(home, "codes", "chromedriver-mac-arm64"),
            "chromedriver",
        )
    return (
        os.path.join(home, "codes", "chrome-linux64", "chrome"),
        os.path.join(home, "codes", "chromedriver-linux64"),
        "chromedriver",
    )
