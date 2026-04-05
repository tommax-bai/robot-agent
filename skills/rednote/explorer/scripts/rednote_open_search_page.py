from utils.init_functions.init_chrome_client import get_chrome
import time
import random
import urllib.parse

def rednote_open_search_page(trace_id: str, keyword: str = "招聘"):
    """
    打开小红书搜索结果页
    """
    chrome_client = get_chrome()
    
    # 编码关键词
    encoded_keyword = urllib.parse.quote(keyword)
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded_keyword}&source=web_explore_feed"
    
    # 清理旧的小红书标签页，保持环境干净
    tabs = chrome_client.list_tabs()
    for tab in tabs:
        if "xiaohongshu.com" in tab.get("url", ""):
            chrome_client.close_tab(tab["id"])
            
    chrome_client.new_tab(search_url)
    
    # 等待加载
    time.sleep(random.uniform(3, 5))
    
    return {
        "ok": True,
        "message": f"已打开小红书搜索页: {keyword}",
        "finish": False
    }
