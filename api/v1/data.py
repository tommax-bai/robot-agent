"""只读数据管理端点：给 dashboard 的 Data 面板用。

直接读当前 agent 实际写入的存储（JSON 文件 + worker.state），不做写操作。
后续若切到 SQLite 主路径，只需把 worker.state 背后的 Repo 换掉即可，端点不动。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.v1._common import resolve_worker

router = APIRouter()

_HISTORY_ROOT = Path("history")


# ─── agent_state ─────────────────────────────────────────────

@router.get("/data/agent_state")
async def get_agent_state(account_id: str | None = None):
    """返回指定账号的完整 agent_state（mood / inspiration_pool / hashtags / ...）。"""
    worker = resolve_worker(account_id)
    if worker.state is None:
        raise HTTPException(status_code=404, detail=f"worker={worker.account_id} 无 state（session-only 拓扑）")
    state = worker.state.get()
    return {
        "ok": True,
        "account_id": worker.account_id,
        "state": state,
    }


# ─── tasks ───────────────────────────────────────────────────

def _parse_index_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def _parse_md_meta(md_path: Path) -> dict:
    """从 .md 的 YAML frontmatter 抽 status / created_at / goal。"""
    if not md_path.exists():
        return {}
    try:
        raw = md_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not raw.startswith("---\n"):
        return {}
    end = raw.find("\n---\n", 4)
    if end < 0:
        return {}
    try:
        import yaml
        meta = yaml.safe_load(raw[4:end]) or {}
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


@router.get("/data/tasks")
async def list_tasks(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """任务历史列表（倒序，最新在前）。

    来源：history/index.jsonl（trace 索引）+ history/{trace_id}.md（状态 frontmatter）。
    """
    index_path = _HISTORY_ROOT / "index.jsonl"
    if not index_path.exists():
        return {"ok": True, "total": 0, "limit": limit, "offset": offset, "data": []}

    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 history/index.jsonl 失败: {e}")

    rows: list[dict] = []
    for line in reversed(lines):  # 倒序：最新在前
        entry = _parse_index_line(line)
        if entry is None:
            continue
        rows.append(entry)

    total = len(rows)
    page = rows[offset : offset + limit]

    # 每条补上 md frontmatter 里的 status / goal（I/O 控制在 limit 条内）
    enriched: list[dict] = []
    for r in page:
        trace_id = r.get("trace_id", "")
        meta = _parse_md_meta(_HISTORY_ROOT / f"{trace_id}.md") if trace_id else {}
        enriched.append({
            "trace_id": trace_id,
            "dt": r.get("dt", ""),
            "goal": meta.get("goal", ""),
            "status": meta.get("status", ""),
            "account_id": meta.get("account_id", ""),
        })

    return {"ok": True, "total": total, "limit": limit, "offset": offset, "data": enriched}


@router.get("/data/tasks/{trace_id}")
async def get_task(trace_id: str):
    """单个任务的 markdown 正文 + frontmatter（给 drill-down 看详情）。"""
    safe = trace_id.replace("/", "").replace("..", "")
    md_path = _HISTORY_ROOT / f"{safe}.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail=f"任务 {trace_id} 无历史文件")
    try:
        raw = md_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取失败: {e}")

    meta: dict = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end > 0:
            try:
                import yaml
                meta = yaml.safe_load(raw[4:end]) or {}
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
            body = raw[end + 5 :]

    return {"ok": True, "trace_id": trace_id, "meta": meta, "body": body}


# ─── token usage ─────────────────────────────────────────────
# 注：按天统计 / 分页明细 / 可查日期已有 /api/v1/usage/* 端点，
# 这里提供一个聚合视图给 dashboard 一次拿齐。

@router.get("/data/token_usage/summary")
async def token_usage_summary(days: int = Query(7, ge=1, le=30)):
    """最近 N 天每天的 total / prompt / completion / call_count。"""
    from datetime import datetime, timedelta

    log_dir = Path("logs/token_usage")
    if not log_dir.exists():
        return {"ok": True, "days": days, "data": []}

    today = datetime.now().date()
    buckets: list[dict] = []
    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        log_file = log_dir / f"usage_{date_str}.jsonl"
        daily = {"date": date_str, "total": 0, "prompt": 0, "completion": 0, "calls": 0, "models": {}}
        if log_file.exists():
            try:
                for line in log_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    daily["total"] += int(e.get("total", 0) or 0)
                    daily["prompt"] += int(e.get("prompt", 0) or 0)
                    daily["completion"] += int(e.get("completion", 0) or 0)
                    daily["calls"] += 1
                    m = e.get("model") or "unknown"
                    daily["models"][m] = daily["models"].get(m, 0) + int(e.get("total", 0) or 0)
            except Exception:
                pass
        buckets.append(daily)
    # 按日期倒序（最新在前）
    buckets.sort(key=lambda b: b["date"], reverse=True)
    return {"ok": True, "days": days, "data": buckets}
