from utils.init_functions.init_chrome_client import get_chrome
import utils.logger as logger
import time

def cleanup_chrome_environment(trace_id: str = "system"):
    """
    强行清理浏览器环境：关闭所有标签页，只保留一个空白页。
    """
    try:
        chrome_client = get_chrome()
        tabs = chrome_client.list_tabs()
        
        logger.info({"msg": "开始清理浏览器环境", "current_tabs_count": len(tabs)}, trace_id)
        
        # 1. 先开一个绝对干净的空白页
        chrome_client.new_tab("about:blank")
        
        # 2. 关闭之前所有的旧标签页
        for tab in tabs:
            try:
                chrome_client.close_tab(tab["id"])
            except:
                pass
        
        time.sleep(1) # 等待浏览器反应
        logger.info({"msg": "浏览器环境清理完成"}, trace_id)
        return True
    except Exception as e:
        logger.error({"msg": "浏览器环境清理失败", "error": str(e)}, trace_id)
        return False
