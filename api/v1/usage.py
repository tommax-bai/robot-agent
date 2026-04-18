"""Token 用量查询：按天统计、明细分页、可查日期列表。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Query

router = APIRouter()

_LOG_DIR = Path("logs/token_usage")


@router.get("/usage/daily_stats")
async def daily_stats(date: str | None = Query(None, description="YYYY-MM-DD，不传默认今天")):
    """指定日期的 Token 消耗总量统计。"""
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
            m = entry.get("model", "unknown")
            stats["models"][m] = stats["models"].get(m, 0) + entry.get("total", 0)
        return {"ok": True, "data": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/usage/details")
async def details(
    date: str | None = Query(None, description="YYYY-MM-DD，不传默认今天"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """分页查询明细，倒序返回。"""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    log_file = _LOG_DIR / f"usage_{date}.jsonl"
    if not log_file.exists():
        return {"ok": True, "data": [], "total": 0, "page": page, "message": "该日期暂无记录"}

    try:
        records = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records.reverse()
        start = (page - 1) * page_size
        return {
            "ok": True,
            "total": len(records),
            "page": page,
            "page_size": page_size,
            "data": records[start : start + page_size],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/usage/list_dates")
async def list_dates():
    """所有有日志的日期（倒序）。"""
    if not _LOG_DIR.exists():
        return {"ok": True, "data": []}
    dates = sorted(
        [p.stem.replace("usage_", "") for p in _LOG_DIR.glob("usage_*.jsonl")],
        reverse=True,
    )
    return {"ok": True, "data": dates}
