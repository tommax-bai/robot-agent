from __future__ import annotations

import config
import utils.init_functions.init_chrome_client as init_chrome_client
import utils.init_functions.init_chrome_config as init_chrome_config
import utils.init_functions.init_screen_size as init_screen_size
import utils.init_functions.init_system_info as init_system_info
import utils.logger as logger


def init() -> None:
    init_screen_size.init_screen_size()
    init_system_info.init_system_info()
    # Chrome 只在 local_chrome 模式下启动；云端模式（cloudmobile / agentbay）
    # 通过 AgentBay session 操作云手机，不需要本地 Chrome
    mode = config.agent["runtime"]["mode"]
    if mode == "local_chrome":
        init_chrome_config.init_chrome_config()
        init_chrome_client.init_chrome_client()
    else:
        logger.info({"msg": f"runtime.mode={mode}，跳过本地 Chrome 启动"})
