"""
JSON 解析工具：从 LLM 原始输出中提取和归一化 JSON。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any


class JsonExtractionError(ValueError):
    def __init__(self, raw_text: str, errors: list[json.JSONDecodeError]):
        self.raw_text = raw_text
        self.errors = errors
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if not self.errors:
            return f"无法从输出中提取有效的 JSON。raw_len={len(self.raw_text)} raw_preview={self.raw_text[:200]}"

        error = self.errors[-1]
        return (
            "无法从输出中提取有效的 JSON。"
            f"原因={error.msg} line={error.lineno} col={error.colno} pos={error.pos} "
            f"raw_len={len(self.raw_text)} error_near={_excerpt(error.doc, error.pos)}"
        )


def normalize_dict_keys(obj):
    """递归归一化字典键名，移除多余的引号和空格"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if isinstance(k, str):
                new_key = k.strip().strip('"').strip("'").strip()
                new_obj[new_key] = normalize_dict_keys(v)
            else:
                new_obj[k] = normalize_dict_keys(v)
        return new_obj
    elif isinstance(obj, list):
        return [normalize_dict_keys(i) for i in obj]
    else:
        return obj


def extract_json(raw_text: str) -> dict | list:
    """从原始文本中提取 JSON 对象，支持 Markdown 代码块包裹，并具备更强的容错能力"""
    if not raw_text:
        raise ValueError("模型输出为空")

    text = raw_text.strip()
    errors: list[json.JSONDecodeError] = []

    for candidate in _json_candidates(text):
        for repaired in _repair_candidates(candidate):
            try:
                return normalize_dict_keys(json.loads(repaired))
            except json.JSONDecodeError as e:
                errors.append(e)
                parsed = _try_parse_python_literal(repaired)
                if parsed is not None:
                    return normalize_dict_keys(parsed)

    raise JsonExtractionError(raw_text, errors)


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    stripped = _strip_code_fence(text)
    if stripped not in candidates:
        candidates.append(stripped)

    balanced = _extract_balanced_json(stripped)
    if balanced and balanced not in candidates:
        candidates.append(balanced)

    greedy = _extract_greedy_json(stripped)
    if greedy and greedy not in candidates:
        candidates.append(greedy)
    return candidates


def _repair_candidates(text: str) -> list[str]:
    candidates = [text.strip()]

    no_trailing_commas = re.sub(r',\s*([}\]])', r'\1', candidates[-1])
    if no_trailing_commas not in candidates:
        candidates.append(no_trailing_commas)

    # 对象/数组间缺逗号：}{ -> },{  ][ -> ],[  }[ -> },[  ]{ -> ],{
    missing_commas = re.sub(r'([}\]])\s*([{\[])', r'\1,\2', candidates[-1])
    if missing_commas not in candidates:
        candidates.append(missing_commas)

    # 字符串值中未转义的中文书名号式引号（如 “reason”: “点击”我”图标”）
    unescaped_cn_quotes = candidates[-1].replace('\u201c', '\u300c').replace('\u201d', '\u300d')
    if unescaped_cn_quotes not in candidates:
        candidates.append(unescaped_cn_quotes)

    unescaped = re.sub(r'\\”', '”', candidates[-1])
    if unescaped not in candidates:
        candidates.append(unescaped)

    smart_quotes = candidates[-1].translate(str.maketrans({'\u201c': '”', '\u201d': '”'}))
    if smart_quotes not in candidates:
        candidates.append(smart_quotes)

    return candidates


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _extract_balanced_json(text: str) -> str:
    start = _first_json_start(text)
    if start < 0:
        return ""

    opener = text[start]
    closer = "}" if opener == "{" else "]"
    stack = [closer]
    in_string = False
    escaped = False

    for idx in range(start + 1, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or char != stack[-1]:
                return ""
            stack.pop()
            if not stack:
                return text[start : idx + 1]

    return ""


def _extract_greedy_json(text: str) -> str:
    start = _first_json_start(text)
    if start < 0:
        return ""
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        return ""
    return text[start : end + 1]


def _first_json_start(text: str) -> int:
    starts = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
    return min(starts) if starts else -1


def _try_parse_python_literal(text: str) -> Any:
    if not text.startswith(("{", "[")):
        return None
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _excerpt(text: str, pos: int, radius: int = 80) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return text[start:end].replace("\n", "\\n")
