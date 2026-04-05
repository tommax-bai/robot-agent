from utils.init_functions.init_chrome_client import get_chrome
import time
import random
import utils.logger as logger

def open_douyin_homepage(trace_id: str):
    """
    打开抖音精选首页。
    策略：
    1. 检查是否已有抖音标签页，有则切换。
    2. 如果没有，则通过 API 打开新标签页，并强制切换视觉焦点。
    """
    chrome_client = get_chrome()
    tabs = chrome_client.list_tabs()

    # 1. 寻找现有标签页
    for tab in tabs:
        if "抖音" in tab["title"] or "douyin.com" in tab["url"]:
            logger.info({"msg": "发现已有抖音标签页，正在切换", "tab_id": tab["id"]}, trace_id)
            chrome_client.switch_to_tab(tab["id"])
            return {
                "ok": True,
                "message": "已切换到现有的抖音标签页",
                "finish": False
            }

    # 2. 如果没有，通过 API 打开新标签页
    target_url = "https://www.douyin.com/jingxuan"
    logger.info({"msg": "未发现抖音标签页，通过 API 打开新页面", "url": target_url}, trace_id)

    # 关键：获取新标签页 ID 并立即切换，否则 Agent 的"眼睛"还停留在 about:blank
    new_tab_id = chrome_client.new_tab(target_url)
    if new_tab_id:
        chrome_client.switch_to_tab(new_tab_id)

    # 等待页面加载
    time.sleep(random.uniform(4, 6))

    return {
        "ok": True,
        "message": "已成功打开并切换到抖音精选首页",
        "finish": False
    }
