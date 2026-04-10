"""
任务历史记录模块：负责初始化任务的 trace 文件和索引。
"""
import os
import json
import yaml
from datetime import datetime
import utils.logger as logger


def init_history(trace_id: str, user_goal: str):
    """任务开始时初始化历史记录：写入 index.jsonl 并创建 trace_id.md 占位"""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. 写入 index.jsonl (索引)
        history_index_path = "history/index.jsonl"
        if not os.path.exists("history"):
            os.makedirs("history")
        entry = {"dt": now, "trace_id": trace_id}
        with open(history_index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # 2. 创建 trace_id.md 初始占位
        md_path = f"history/{trace_id}.md"
        meta = {
            "trace_id": trace_id,
            "created_at": now,
            "goal": user_goal,
            "status": "running"
        }
        yaml_content = yaml.dump(meta, allow_unicode=True, sort_keys=False)
        content = f"---\n{yaml_content}---\n\n# Task History: {trace_id}\n"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)

    except Exception as e:
        logger.error({"msg": "初始化历史记录失败", "error": str(e)})
