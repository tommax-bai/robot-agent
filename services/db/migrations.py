"""Schema 版本迁移 + 从 JSON 导入历史数据。"""

from __future__ import annotations

from datetime import datetime

import utils.logger as logger
from services.db.connection import Db
from services.db.schema import MIGRATIONS, SCHEMA_VERSION


def _current_version(db: Db) -> int:
    try:
        row = db.read().execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        if row and row["v"] is not None:
            return int(row["v"])
    except Exception:
        pass
    return 0


def ensure_schema(db: Db) -> None:
    """幂等：检查当前 schema 版本，跑未执行的迁移。"""
    # 先确保 schema_version 表存在（第一次 boot）
    for sql in MIGRATIONS[1]:
        if "CREATE TABLE IF NOT EXISTS schema_version" in sql:
            db.read().execute(sql)
            break

    current = _current_version(db)
    if current >= SCHEMA_VERSION:
        return

    logger.info({"msg": "SQLite schema 迁移开始", "from": current, "to": SCHEMA_VERSION})
    with db.write() as conn:
        for v in range(current + 1, SCHEMA_VERSION + 1):
            for sql in MIGRATIONS[v]:
                conn.execute(sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (v, datetime.now().isoformat()),
            )
    logger.info({"msg": "SQLite schema 迁移完成", "version": SCHEMA_VERSION})


def already_imported(db: Db, source: str) -> bool:
    row = db.read().execute(
        "SELECT 1 FROM import_log WHERE source = ?", (source,)
    ).fetchone()
    return row is not None


def mark_imported(db: Db, source: str, rows: int) -> None:
    with db.write() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO import_log(source, imported_at, rows) VALUES (?, ?, ?)",
            (source, datetime.now().isoformat(), rows),
        )


def import_from_json_if_empty(db: Db) -> dict[str, int]:
    """首次迁移：已有 JSON 文件，SQLite 空表 → 导入。返回 {表名: 导入行数}。"""
    import json
    from pathlib import Path

    counts: dict[str, int] = {}

    # token_usage: logs/token_usage/usage_*.jsonl
    if not already_imported(db, "token_usage"):
        rows = _import_token_usage(db, Path("logs/token_usage"))
        counts["token_usage"] = rows
        mark_imported(db, "token_usage", rows)

    # tasks: history/index.jsonl + history/{trace}.md
    if not already_imported(db, "tasks"):
        rows = _import_tasks(db, Path("history"))
        counts["tasks"] = rows
        mark_imported(db, "tasks", rows)

    # agent_state: data/agent_state.json + data/accounts/*/agent_state.json
    if not already_imported(db, "agent_state"):
        rows = _import_agent_state(db, Path("data"))
        counts["agent_state"] = rows
        mark_imported(db, "agent_state", rows)

    # action_traces: data/action_traces/*.jsonl
    if not already_imported(db, "action_traces"):
        rows = _import_action_traces(db, Path("data/action_traces"))
        counts["action_traces"] = rows
        mark_imported(db, "action_traces", rows)

    # recipes: data/action_recipes/ + data/action_recipe_candidates/
    if not already_imported(db, "recipes"):
        rows = _import_recipes(db, Path("data/action_recipes"), Path("data/action_recipe_candidates"))
        counts["recipes"] = rows
        mark_imported(db, "recipes", rows)

    # pages: data/page_registry/pages.json
    if not already_imported(db, "pages"):
        rows = _import_pages(db, Path("data/page_registry/pages.json"))
        counts["pages"] = rows
        mark_imported(db, "pages", rows)

    return counts


def _import_pages(db: Db, pages_file: Path) -> int:
    import json
    if not pages_file.exists():
        return 0
    logger.info({"msg": "pages 导入开始", "source": str(pages_file)})
    try:
        data = json.loads(pages_file.read_text(encoding="utf-8"))
    except Exception as ex:
        logger.warning({"msg": "pages 导入失败", "error": str(ex)})
        return 0
    pages = data.get("pages", data)
    if not isinstance(pages, dict):
        return 0
    now = datetime.now().isoformat()
    inserted = 0
    with db.write() as conn:
        for key, v in pages.items():
            page_state = v.get("page_state") or key
            conn.execute(
                """
                INSERT OR IGNORE INTO pages (
                    page_state, description, layout_type, evidence_json,
                    stable_landmarks_json, dynamic_regions_json, negative_landmarks_json,
                    first_seen_at, last_seen_at, seen_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_state,
                    v.get("description", ""),
                    v.get("layout_type", ""),
                    json.dumps(v.get("evidence") or [], ensure_ascii=False),
                    json.dumps(v.get("stable_landmarks") or [], ensure_ascii=False),
                    json.dumps(v.get("dynamic_regions") or [], ensure_ascii=False),
                    json.dumps(v.get("negative_landmarks") or [], ensure_ascii=False),
                    v.get("first_seen_at") or now,
                    v.get("last_seen_at") or now,
                    int(v.get("seen_count") or 0),
                ),
            )
            for h in (v.get("screenshot_hashes") or []):
                conn.execute(
                    "INSERT OR IGNORE INTO page_screenshot_hashes (page_state, hash, seen_at) VALUES (?, ?, ?)",
                    (page_state, h, now),
                )
            inserted += 1
    logger.info({"msg": "pages 导入完成", "rows": inserted})
    return inserted


def _import_recipes(db: Db, root: Path, candidate_root: Path) -> int:
    import json
    now = datetime.now().isoformat()
    inserted = 0
    sources: list[tuple[Path, bool]] = []
    if root.exists():
        sources.extend((p, False) for p in sorted(root.glob("**/*.json")))
    if candidate_root.exists():
        sources.extend((p, True) for p in sorted(candidate_root.glob("**/*.json")))
    if not sources:
        return 0
    logger.info({"msg": "recipes 导入开始", "sources": [str(root), str(candidate_root)], "files": len(sources)})
    with db.write() as conn:
        for path, trial_default in sources:
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except Exception as ex:
                logger.warning({"msg": "recipe 导入失败", "file": str(path), "error": str(ex)})
                continue
            rid = str(d.get("id") or path.stem)
            origin_trace = None
            origin_subtask = None
            if trial_default:
                parts = path.relative_to(candidate_root).parts
                if len(parts) >= 2:
                    origin_trace = parts[0]
                    origin_subtask = parts[1].replace("_llm.json", "")
            conn.execute(
                """
                INSERT OR IGNORE INTO recipes (
                    id, intent, page_state, required_skill, enabled, trial,
                    confidence, min_confidence, trial_successes, trial_failures,
                    match_keywords_json, summary, steps_json, extra_json,
                    origin_trace_id, origin_subtask_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    str(d.get("intent", "")),
                    str(d.get("page_state", "any")),
                    d.get("required_skill"),
                    int(bool(d.get("enabled", not trial_default))),
                    int(bool(d.get("trial", trial_default))),
                    float(d.get("confidence", 1.0)),
                    float(d.get("min_confidence", 0.8)),
                    int(d.get("trial_successes", 0)),
                    int(d.get("trial_failures", 0)),
                    json.dumps(d.get("match_keywords") or [], ensure_ascii=False),
                    str(d.get("summary", "")),
                    json.dumps(d.get("steps") or [], ensure_ascii=False),
                    "{}",
                    origin_trace,
                    origin_subtask,
                    now,
                    now,
                ),
            )
            inserted += 1
    logger.info({"msg": "recipes 导入完成", "rows": inserted})
    return inserted


def _import_action_traces(db: Db, traces_dir) -> int:
    import json
    if not traces_dir.exists():
        return 0
    logger.info({"msg": "action_traces 导入开始", "source": str(traces_dir)})
    inserted = 0
    with db.write() as conn:
        for path in sorted(traces_dir.glob("*.jsonl")):
            trace_id = path.stem
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    st = e.get("subtask") or {}
                    conn.execute(
                        """
                        INSERT INTO action_traces
                            (trace_id, subtask_id, step, event, time, page_state, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trace_id,
                            st.get("id") if isinstance(st, dict) else None,
                            e.get("step"),
                            e.get("event", ""),
                            e.get("time", ""),
                            e.get("page_state"),
                            line,
                        ),
                    )
                    inserted += 1
            except Exception as ex:
                logger.warning({"msg": "action_traces 导入异常", "file": str(path), "error": str(ex)})
    logger.info({"msg": "action_traces 导入完成", "rows": inserted})
    return inserted


def _import_agent_state(db: Db, data_dir) -> int:
    import json
    from services.agent_state import DEFAULT_STATE
    inserted = 0
    sources: list[tuple[str, object]] = []
    single = data_dir / "agent_state.json"
    if single.exists():
        sources.append(("default", single))
    accts_dir = data_dir / "accounts"
    if accts_dir.exists():
        for acct_dir in accts_dir.iterdir():
            state_file = acct_dir / "agent_state.json"
            if state_file.exists():
                sources.append((acct_dir.name, state_file))
    if not sources:
        return 0
    logger.info({"msg": "agent_state 导入开始", "files": len(sources)})
    now = datetime.now().isoformat()
    with db.write() as conn:
        for account_id, path in sources:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as ex:
                logger.warning({"msg": "agent_state 导入失败", "file": str(path), "error": str(ex)})
                continue
            s = dict(DEFAULT_STATE)
            s.update(raw)
            list_cols = [
                "inspiration_pool", "title_few_shots", "hashtags",
                "anxiety_keywords", "knowledge_topics", "learning_notes",
                "recent_searches", "followers_history",
            ]
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_state (
                    account_id, mood, last_discovery, last_check_time,
                    last_post_date, last_post_trace_id, daily_stats_json,
                    inspiration_pool_json, title_few_shots_json, hashtags_json,
                    anxiety_keywords_json, knowledge_topics_json, learning_notes_json,
                    recent_searches_json, followers_history_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    s.get("mood", "neutral"),
                    s.get("last_discovery", ""),
                    s.get("last_check_time", ""),
                    s.get("last_post_date", ""),
                    s.get("last_post_trace_id", ""),
                    json.dumps(s.get("daily_stats", {}), ensure_ascii=False),
                    *[json.dumps(s.get(col, []), ensure_ascii=False) for col in list_cols],
                    now,
                ),
            )
            inserted += 1
    logger.info({"msg": "agent_state 导入完成", "rows": inserted})
    return inserted


def _import_token_usage(db: Db, log_dir) -> int:
    import json
    if not log_dir.exists():
        return 0
    logger.info({"msg": "token_usage 导入开始", "source": str(log_dir)})
    inserted = 0
    with db.write() as conn:
        for path in sorted(log_dir.glob("usage_*.jsonl")):
            date_str = path.stem.replace("usage_", "")
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    e = json.loads(line)
                    ts = e.get("timestamp", "")
                    logged_at = f"{date_str}T{ts}" if ts else date_str
                    conn.execute(
                        """
                        INSERT INTO token_usage
                            (account_id, date, timestamp, logged_at, trace_id, model,
                             prompt_tokens, completion_tokens, total_tokens)
                        VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            date_str,
                            ts,
                            logged_at,
                            e.get("trace_id", ""),
                            e.get("model", ""),
                            int(e.get("prompt", 0) or 0),
                            int(e.get("completion", 0) or 0),
                            int(e.get("total", 0) or 0),
                        ),
                    )
                    inserted += 1
            except Exception as ex:
                logger.warning({"msg": "token_usage 导入行异常", "file": str(path), "error": str(ex)})
    logger.info({"msg": "token_usage 导入完成", "rows": inserted})
    return inserted


def _import_tasks(db: Db, history_dir) -> int:
    import json
    index_path = history_dir / "index.jsonl"
    if not index_path.exists():
        return 0
    logger.info({"msg": "tasks 导入开始", "source": str(index_path)})
    inserted = 0
    with db.write() as conn:
        try:
            for line in index_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                trace_id = e.get("trace_id", "")
                if not trace_id:
                    continue
                dt = e.get("dt", "")
                md_path = history_dir / f"{trace_id}.md"
                body = ""
                goal = ""
                status = "running"
                if md_path.exists():
                    raw = md_path.read_text(encoding="utf-8")
                    if raw.startswith("---\n"):
                        end = raw.find("\n---\n", 4)
                        if end > 0:
                            try:
                                import yaml
                                meta = yaml.safe_load(raw[4:end]) or {}
                                goal = meta.get("goal", "")
                                status = meta.get("status", "running")
                            except Exception:
                                pass
                            body = raw[end + 5:]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tasks
                        (trace_id, account_id, created_at, goal, status, markdown_body)
                    VALUES (?, 'default', ?, ?, ?, ?)
                    """,
                    (trace_id, dt, goal, status, body),
                )
                inserted += 1
        except Exception as ex:
            logger.warning({"msg": "tasks 导入异常", "error": str(ex)})
    logger.info({"msg": "tasks 导入完成", "rows": inserted})
    return inserted
