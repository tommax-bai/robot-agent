"""SQLite schema 定义（所有 CREATE TABLE / CREATE INDEX）。"""

from __future__ import annotations

SCHEMA_VERSION = 2

# 按 schema 版本索引的 DDL；v1 是初始全量建表
MIGRATIONS: dict[int, list[str]] = {
    1: [
        # schema 版本追踪
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """,
        # 导入标记（每个 JSON 文件只导入一次）
        """
        CREATE TABLE IF NOT EXISTS import_log (
            source TEXT PRIMARY KEY,
            imported_at TEXT NOT NULL,
            rows INTEGER NOT NULL DEFAULT 0
        )
        """,
        # agent_state: 每个 account_id 一行
        """
        CREATE TABLE IF NOT EXISTS agent_state (
            account_id TEXT PRIMARY KEY,
            mood TEXT NOT NULL DEFAULT 'neutral',
            last_discovery TEXT NOT NULL DEFAULT '',
            last_check_time TEXT NOT NULL DEFAULT '',
            last_post_date TEXT NOT NULL DEFAULT '',
            last_post_trace_id TEXT NOT NULL DEFAULT '',
            daily_stats_json TEXT NOT NULL DEFAULT '{"date":"","posts_count":0,"replies_count":0}',
            inspiration_pool_json TEXT NOT NULL DEFAULT '[]',
            title_few_shots_json TEXT NOT NULL DEFAULT '[]',
            hashtags_json TEXT NOT NULL DEFAULT '[]',
            anxiety_keywords_json TEXT NOT NULL DEFAULT '[]',
            knowledge_topics_json TEXT NOT NULL DEFAULT '[]',
            learning_notes_json TEXT NOT NULL DEFAULT '[]',
            recent_searches_json TEXT NOT NULL DEFAULT '[]',
            followers_history_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """,
        # token_usage: LLM 调用日志
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL DEFAULT 'default',
            date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            logged_at TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL,
            completion_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_token_usage_date ON token_usage(date, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_token_usage_trace ON token_usage(trace_id)",
        # tasks: 任务历史
        """
        CREATE TABLE IF NOT EXISTS tasks (
            trace_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            finished_at TEXT,
            markdown_body TEXT NOT NULL DEFAULT ''
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account_id, created_at DESC)",
        # action_traces: per-step 事件
        """
        CREATE TABLE IF NOT EXISTS action_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            subtask_id TEXT,
            step INTEGER,
            event TEXT NOT NULL,
            time TEXT NOT NULL,
            page_state TEXT,
            payload_json TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_traces_trace ON action_traces(trace_id, id)",
        "CREATE INDEX IF NOT EXISTS idx_traces_subtask ON action_traces(trace_id, subtask_id)",
        "CREATE INDEX IF NOT EXISTS idx_traces_event ON action_traces(trace_id, event)",
        # recipes
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            intent TEXT NOT NULL,
            page_state TEXT NOT NULL DEFAULT 'any',
            required_skill TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            trial INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 1.0,
            min_confidence REAL NOT NULL DEFAULT 0.8,
            trial_successes INTEGER NOT NULL DEFAULT 0,
            trial_failures INTEGER NOT NULL DEFAULT 0,
            match_keywords_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '',
            steps_json TEXT NOT NULL,
            extra_json TEXT NOT NULL DEFAULT '{}',
            origin_trace_id TEXT,
            origin_subtask_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_recipes_intent ON recipes(intent)",
        "CREATE INDEX IF NOT EXISTS idx_recipes_page_state ON recipes(page_state)",
        "CREATE INDEX IF NOT EXISTS idx_recipes_trial_enabled ON recipes(trial, enabled)",
        # pages: 语义页面库
        """
        CREATE TABLE IF NOT EXISTS pages (
            page_state TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            layout_type TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            stable_landmarks_json TEXT NOT NULL DEFAULT '[]',
            dynamic_regions_json TEXT NOT NULL DEFAULT '[]',
            negative_landmarks_json TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS page_screenshot_hashes (
            page_state TEXT NOT NULL,
            hash TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (page_state, hash),
            FOREIGN KEY (page_state) REFERENCES pages(page_state) ON DELETE CASCADE
        )
        """,
    ],
    # v2: 补充热点查询索引（token_usage 按 account+日期聚合；tasks 按 status 列表）
    2: [
        "CREATE INDEX IF NOT EXISTS idx_token_usage_account_date ON token_usage(account_id, date, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at DESC)",
    ],
}
