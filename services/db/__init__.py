"""SQLite 持久化层。

职责：
- connection.py  — 全局单例 Db（WAL 模式 + 写锁）
- schema.py      — DDL（所有 CREATE TABLE）
- migrations.py  — schema 版本管理 + JSON 数据导入
- repos/         — per-domain 仓储（StateRepo / TokenUsageRepo / ...）
"""
