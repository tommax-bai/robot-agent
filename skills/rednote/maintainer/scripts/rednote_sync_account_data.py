import json
import os
import datetime
import utils.logger as logger
import config

STATE_FILE = config.agent["maintenance"]["state_file"]
LIMITS = config.agent["state_limits"]

def _merge_and_trim(existing: list, new_items: list, limit: int) -> list:
    """有序去重追加，超出上限时从头部淘汰最旧的条目。"""
    merged = existing + [x for x in new_items if x not in existing]
    return merged[-limit:]

def rednote_sync_account_data(followers: int = None, likes: int = None, is_posted: bool = False, inspiration_pool: list = None, last_discovery: str = None, title_few_shots: list = None, mood: str = None, learning_notes: list = None, hashtags: list = None, anxiety_keywords: list = None, knowledge_topics: list = None, recent_searches: list = None, trace_id: str = "system"):
    """
    同步账号数据、灵感池、爆款标题库、心情、学习笔记、话题词、焦虑点、知识点及搜索历史到本地状态文件。
    """
    try:
        # 1. 读取现有状态
        state = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
        
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # 2. 更新粉丝历史
        if followers is not None:
            history_entry = {
                "date": today_str,
                "time": now.strftime("%H:%M:%S"),
                "followers": followers,
                "likes": likes
            }
            if "followers_history" not in state:
                state["followers_history"] = []
            state["followers_history"].append(history_entry)
            limit = LIMITS.get("followers_history", 30)
            if len(state["followers_history"]) > limit:
                state["followers_history"] = state["followers_history"][-limit:]
        
        # 3. 更新学习成果与状态
        if inspiration_pool is not None:
            state["inspiration_pool"] = _merge_and_trim(state.get("inspiration_pool", []), inspiration_pool, LIMITS["inspiration_pool"])
        if last_discovery is not None:
            state["last_discovery"] = last_discovery
        if title_few_shots is not None:
            state["title_few_shots"] = _merge_and_trim(state.get("title_few_shots", []), title_few_shots, LIMITS["title_few_shots"])
        
        if hashtags is not None:
            state["hashtags"] = _merge_and_trim(state.get("hashtags", []), hashtags, LIMITS["hashtags"])

        if anxiety_keywords is not None:
            state["anxiety_keywords"] = _merge_and_trim(state.get("anxiety_keywords", []), anxiety_keywords, LIMITS["anxiety_keywords"])
        
        if knowledge_topics is not None:
            state["knowledge_topics"] = _merge_and_trim(state.get("knowledge_topics", []), knowledge_topics, LIMITS["knowledge_topics"])

        if mood is not None:
            state["mood"] = mood
        if learning_notes is not None:
            state["learning_notes"] = _merge_and_trim(state.get("learning_notes", []), learning_notes, LIMITS["learning_notes"])

        if recent_searches is not None:
            state["recent_searches"] = _merge_and_trim(state.get("recent_searches", []), recent_searches, LIMITS["recent_searches"])

        # 4. 更新今日发帖状态
        if "daily_stats" not in state or state["daily_stats"].get("date") != today_str:
            state["daily_stats"] = {
                "date": today_str,
                "posts_count": 0,
                "replies_count": 0
            }
        
        if is_posted:
            state["daily_stats"]["posts_count"] += 1
            state["last_post_date"] = today_str
            state["last_post_trace_id"] = trace_id

        state["last_check_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # 4. 写回文件
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=4)
            
        logger.info({"msg": "账号数据同步成功", "today": today_str, "followers": followers}, trace_id)
        return {"ok": True, "msg": "数据已同步"}
        
    except Exception as e:
        logger.error({"msg": "同步账号数据失败", "error": str(e)}, trace_id)
        return {"ok": False, "error": str(e)}

def get_current_state():
    """读取当前状态，供 Planner 或 Supervisor 决策使用"""
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)
