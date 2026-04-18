"""带 YAML frontmatter 的 prompt 模板加载。"""

from __future__ import annotations

import yaml

import utils.logger as logger


def load_prompt(path: str) -> tuple[dict, str]:
    """加载 `.md` prompt 文件。返回 (meta, body)；无 frontmatter 时 meta 为 {}。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
            except Exception as e:
                logger.error({"msg": "YAML解析失败", "error": str(e), "path": path})
    return meta, body
