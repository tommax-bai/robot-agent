"""
Token 消耗记录模块：按天写入 JSONL 日志，自动清理过期文件。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import utils.logger as logger

_LOG_DIR = Path("logs/token_usage")


def log_token_usage(trace_id: str, model: str, prompt_tokens: int, completion_tokens: int):
    """记录 Token 消耗到本地 JSONL 文件，按天滚动，保留 7 天。"""
    try:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        _LOG_DIR.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": now.strftime("%H:%M:%S"),
            "trace_id": trace_id,
            "model": model,
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
        }

        log_file = _LOG_DIR / f"usage_{date_str}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        _cleanup_old_usage_logs()

    except Exception as e:
        logger.error({"msg": "Token 记账失败", "error": str(e)}, trace_id)


def _cleanup_old_usage_logs():
    """清理 7 天前的日志文件"""
    try:
        cutoff_date = datetime.now() - timedelta(days=7)
        for path in _LOG_DIR.glob("usage_*.jsonl"):
            try:
                date_part = path.stem.replace("usage_", "")
                file_date = datetime.strptime(date_part, "%Y-%m-%d")
                if file_date < cutoff_date:
                    path.unlink()
                    logger.info({"msg": "已清理过期 Token 日志", "file": path.name})
            except (ValueError, OSError):
                continue
    except Exception as e:
        logger.error({"msg": "清理过期 Token 日志失败", "error": str(e)})
