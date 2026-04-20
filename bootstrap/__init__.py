"""启动编排层：进程冷启动时的一次性平台探测与外部依赖准备。

与 `utils/`（纯工具）的区别：这里有副作用、有顺序依赖、耦合 `config`。
"""

from __future__ import annotations

import config
import utils.logger as logger

from . import chrome_client, chrome_install, chrome_launch, system


def boot() -> None:
    """进程启动入口：按模式探测平台并准备外部依赖。"""
    system.detect_screen()
    system.detect_platform()

    mode = config.settings.agent.runtime.mode
    if mode == "chromelocal":
        chrome_install.ensure_installed()
        chrome_launch.prepare()
        chrome_client.start()
    else:
        logger.info({"msg": f"runtime.mode={mode}，跳过本地 Chrome 启动"})
