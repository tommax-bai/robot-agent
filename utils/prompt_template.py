"""
Prompt 模板加载工具：解析带 YAML frontmatter 的 .md 模板文件。
"""

from __future__ import annotations

import yaml

import utils.logger as logger


def load_prompt_template(template_path: str) -> tuple:
    """
    加载带 YAML frontmatter 的 prompt 模板文件。
    返回 (meta: dict, body: str)
    """
    with open(template_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    meta = {}
    body = raw_content
    if raw_content.startswith("---"):
        parts = raw_content.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
            except Exception as e:
                logger.error({"msg": "YAML解析失败", "error": str(e), "path": template_path})
    return meta, body
