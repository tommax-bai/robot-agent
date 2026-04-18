"""rednote.explorer: 小红书内容探索与收割专家。"""

from __future__ import annotations

import random
import time
import urllib.parse

import utils.logger as logger
from skills import Pack, ToolContext, ToolOutcome
from bootstrap.chrome_client import get as get_chrome

pack = Pack(
    name="rednote.explorer",
    description=(
        "小红书内容探索与收割专家。负责在指定领域内巡逻、发现高赞爆款内容，并执行点赞、收藏及知识收割。"
        "视频笔记仅允许浏览，不点赞不收藏。"
    ),
    supports=("local_chrome",),
)


@pack.tool
def rednote_open_search_page(ctx: ToolContext, keyword: str = "") -> ToolOutcome:
    """打开小红书搜索结果页。keyword 必填，不再使用业务默认值。"""
    if not keyword or not keyword.strip():
        return ToolOutcome.failure(
            "keyword 参数为空：调用 rednote_open_search_page 时必须显式提供 keyword"
        )

    chrome = get_chrome()
    encoded = urllib.parse.quote(keyword.strip())
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded}&source=web_explore_feed"

    for tab in chrome.list_tabs():
        if "xiaohongshu.com" in tab.get("url", ""):
            chrome.close_tab(tab["id"])

    chrome.new_tab(search_url)
    time.sleep(random.uniform(3, 5))

    logger.info({"msg": "已打开小红书搜索页", "keyword": keyword}, ctx.trace_id)
    return ToolOutcome.success(f"已打开小红书搜索页: {keyword}")
