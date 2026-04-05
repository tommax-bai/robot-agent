import os
import json
import glob
from datetime import datetime, timedelta
import utils.logger as logger

def log_token_usage(trace_id: str, model: str, prompt_tokens: int, completion_tokens: int):
    """
    记录 Token 消耗到本地 JSONL 文件，按天滚动，保留 7 天。
    """
    try:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        log_dir = "logs/token_usage"
        log_file = f"{log_dir}/usage_{date_str}.jsonl"
        
        # 1. 确保目录存在
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        # 2. 构造记录
        entry = {
            "timestamp": now.strftime("%H:%M:%S"),
            "trace_id": trace_id,
            "model": model,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens
        }
        
        # 3. 写入文件
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
        # 4. 自动清理 7 天前的文件
        _cleanup_old_usage_logs(log_dir)
        
    except Exception as e:
        # 记账失败不应该中断主流程，但需要记录错误
        logger.error({"msg": "Token 记账失败", "error": str(e)}, trace_id)

def _cleanup_old_usage_logs(log_dir: str):
    """清理 7 天前的日志文件"""
    try:
        retention_days = 7
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        # 匹配所有 usage_YYYY-MM-DD.jsonl 文件
        files = glob.glob(f"{log_dir}/usage_*.jsonl")
        for file_path in files:
            filename = os.path.basename(file_path)
            # 提取日期部分: usage_2026-02-03.jsonl -> 2026-02-03
            try:
                date_part = filename.replace("usage_", "").replace(".jsonl", "")
                file_date = datetime.strptime(date_part, "%Y-%m-%d")
                
                if file_date < cutoff_date:
                    os.remove(file_path)
                    logger.info({"msg": "已清理过期 Token 日志", "file": filename})
            except (ValueError, OSError):
                continue
    except Exception as e:
        logger.error({"msg": "清理过期 Token 日志失败", "error": str(e)})
