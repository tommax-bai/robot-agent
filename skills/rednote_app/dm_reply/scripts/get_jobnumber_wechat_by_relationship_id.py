import httpx

def get_jobnumber_wechat_by_relationship_id(relationship_id: str):
    base_url = "http://192.168.110.31:8000" # 统一 Base URL
    headers = {
        'Authorization': 'Bearer 96e74d38e99ba691a8a9f4d62de1c88d4e1c9b8dccde5dcb6412e6a3cbc78f07',
        'Content-Type': 'application/json',
    }

    # 1. 先获取 relationship 信息
    url_relationship = f"{base_url}/api/v1/rl/{relationship_id}"
    try:
        response = httpx.get(url_relationship, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", {})
        job_number = data.get("job_number")
        
        if not job_number:
            return {
                "ok": False,
                "message": f"获取 job_number 失败，关系ID：{relationship_id}。",
                "finish": False
            }

        # 2. 拿到 job_number 后，再构造并请求 jd 接口
        url_jd = f"{base_url}/api/v1/jd/{job_number}"
        response_jd = httpx.get(url_jd, headers=headers)
        response_jd.raise_for_status()
        wechat_number = response_jd.json().get("data", {}).get("wechat_number")

        if wechat_number:
            return {
                "ok": True,
                "message": f"微信号：{wechat_number}, 职位编号：{job_number}",
                "finish": False
            }
        else:
            return {
                "ok": False,
                "message": "该职位没有配置微信号，停止回复。",
                "finish": False
            }

    except Exception as e:
        return {
            "ok": False,
            "message": f"请求出错: {str(e)}",
            "finish": False
        }