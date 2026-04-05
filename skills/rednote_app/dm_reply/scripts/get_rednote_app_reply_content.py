
import httpx

def get_rednote_app_reply_content(user_id: str, user_content: list[str], job_number: str):
    """
    获取小红书APP的回复内容。
    """
    print(f"user_id: {user_id}, user_content: {user_content}")
    json_payload = {
        "session_id": user_id,
        "platform": "小红书",
        "user_input": "|".join(user_content),
        "job_number": job_number
    }
    return {
                "ok": True,
                "message": f"以下是要回复的内容，请依此回复：哈哈;add_wechat",
        }
    with httpx.Client() as client:
        
        response = client.post(
            url="http://192.168.110.91:9093/redirect/chat",
            json=json_payload,
            timeout=300
        )
        if response.status_code == 200:
            result = response.json()
            content_list = result["content_list"]                                           # 先取出来
            content_list = sorted(content_list, key=lambda x: 1 if x == "add_wechat" else 0)  # 再排序
            content_list = ";".join(content_list) 
            print(f"content_list: {content_list}")    
            # content_list = "您好" + ";add_wechat"                                        # 再拼接
            # content_list = content_list.replace("add_wechat", "您加 V:1502 1298 805")
            # content_list = content_list.replace("add_wechat", "您加 V:1367 9053 047")
            return {
                "ok": True,
                "message": f"以下是要回复的内容，请依此回复：{content_list}",
                "finish": False
             }
        else:
            return {
                "ok": False,
                "message": f"获取回复内容失败，状态码：{response.status_code},请勿回复任何内容，直接跳过处理。",
                "finish": False
            }
