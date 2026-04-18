"""环境变量与文本文件读取的小工具，不直接依赖 config。"""
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    """读取环境变量并 strip。未设置走 default。"""
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    """整数型环境变量。空串/未设置走 default；非数字抛 ValueError。"""
    raw = env(name, "")
    return int(raw) if raw else default


def env_bool(name: str, default: bool) -> bool:
    """布尔型环境变量：接受 true/false/1/0/yes/no（不区分大小写）。"""
    raw = env(name, "").lower()
    if not raw:
        return default
    return raw not in ("false", "0", "no", "off")


def env_executable_path(name: str, default_dir: str, executable_name: str) -> str:
    """环境变量可能指向目录或完整路径；目录时拼上 executable_name。"""
    path = env(name, default_dir)
    if path and os.path.isdir(path):
        return os.path.join(path, executable_name)
    return path


def load_text_file(path: str, default: str = "") -> str:
    """从文件加载文本内容，失败则返回默认值。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default
