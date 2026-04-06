import os
def open_rednote_app():
    """
    打开小红书APP。调用shell执行 open -a "rednote"
    """
    os.system("open -a rednote")
    return {
        "ok": True,
        "message": "已成功打开小红书APP, 小红书APP仅限回复私信操作",
        "finish": False
    }