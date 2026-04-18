"""
Chrome 环境重置：关掉所有旧 tab，留一个空白页。

仅 local_chrome 模式使用。由 Supervisor 在任务切换时调用。
"""

from __future__ import annotations

import time

import utils.logger as logger
from bootstrap.chrome_client import get as get_chrome


def cleanup_chrome_environment(trace_id: str = "system") -> bool:
    try:
        chrome_client = get_chrome()
        tabs = chrome_client.list_tabs()

        logger.info({"msg": "开始清理浏览器环境", "current_tabs_count": len(tabs)}, trace_id)

        chrome_client.new_tab("about:blank")

        for tab in tabs:
            try:
                chrome_client.close_tab(tab["id"])
            except Exception as e:
                logger.warning({"msg": "关闭标签页失败", "tab_id": tab.get("id"), "error": str(e)}, trace_id)

        time.sleep(1)
        logger.info({"msg": "浏览器环境清理完成"}, trace_id)
        return True
    except Exception as e:
        logger.error({"msg": "浏览器环境清理失败", "error": str(e)}, trace_id)
        return False


__all__ = ["cleanup_chrome_environment"]
