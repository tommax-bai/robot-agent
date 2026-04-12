"""
Token 用量查询路由：按天统计、明细分页、可查日期列表。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

router = APIRouter()

_LOG_DIR = Path("logs/token_usage")


@router.get("/usage/daily_stats")
async def get_daily_stats(date: str | None = Query(None, description="查询日期，格式 YYYY-MM-DD，不传默认今天")):
    """查询指定日期的 Token 消耗总量统计"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    log_file = _LOG_DIR / f"usage_{date}.jsonl"

    stats = {
        "date": date,
        "total_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "call_count": 0,
        "models": {},
    }

    if not log_file.exists():
        return {"ok": True, "data": stats, "message": "该日期暂无记录"}

    try:
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            stats["total_tokens"] += entry.get("total", 0)
            stats["total_prompt_tokens"] += entry.get("prompt", 0)
            stats["total_completion_tokens"] += entry.get("completion", 0)
            stats["call_count"] += 1

            model = entry.get("model", "unknown")
            if model not in stats["models"]:
                stats["models"][model] = 0
            stats["models"][model] += entry.get("total", 0)

        return {"ok": True, "data": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/usage/details")
async def get_usage_details(
    date: str | None = Query(None, description="查询日期，格式 YYYY-MM-DD，不传默认今天"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """分页查询指定日期的 Token 消耗明细"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    log_file = _LOG_DIR / f"usage_{date}.jsonl"

    if not log_file.exists():
        return {"ok": True, "data": [], "total": 0, "page": page, "message": "该日期暂无记录"}

    try:
        all_records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        # 倒序排列，最新的在前面
        all_records.reverse()

        total_count = len(all_records)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        return {
            "ok": True,
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "data": all_records[start_idx:end_idx],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/usage/list_dates")
async def list_available_dates():
    """获取当前节点所有可查询的日期列表"""
    if not _LOG_DIR.exists():
        return {"ok": True, "data": []}

    dates = sorted(
        [path.stem.replace("usage_", "") for path in _LOG_DIR.glob("usage_*.jsonl")],
        reverse=True,
    )
    return {"ok": True, "data": dates}
