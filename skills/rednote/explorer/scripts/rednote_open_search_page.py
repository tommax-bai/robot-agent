"""
打开小红书搜索结果页。LLM 必须显式提供 keyword，不再使用业务相关默认值。
"""

from __future__ import annotations

import random
import time
import urllib.parse

import utils.logger as logger
from utils.init_functions.init_chrome_client import get_chrome


def rednote_open_search_page(trace_id: str, keyword: str = ""):
    """
    打开小红书搜索结果页。

    Args:
        keyword: 搜索关键词。必填，调用方必须显式传入。
    """
    if not keyword or not keyword.strip():
        return {
            "ok": False,
            "message": "keyword 参数为空：调用 rednote_open_search_page 时必须显式提供 keyword",
            "finish": False,
        }

    chrome_client = get_chrome()
    encoded_keyword = urllib.parse.quote(keyword.strip())
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded_keyword}&source=web_explore_feed"

    # 清理旧的小红书标签页，保持环境干净
    for tab in chrome_client.list_tabs():
        if "xiaohongshu.com" in tab.get("url", ""):
            chrome_client.close_tab(tab["id"])

    chrome_client.new_tab(search_url)
    time.sleep(random.uniform(3, 5))

    logger.info({"msg": "已打开小红书搜索页", "keyword": keyword}, trace_id)
    return {
        "ok": True,
        "message": f"已打开小红书搜索页: {keyword}",
        "finish": False,
    }
