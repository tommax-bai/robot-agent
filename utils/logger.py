"""统一日志门面。

设计要点：
- 标准库原生：handlers 挂在 root，模块可以用 `logging.getLogger(__name__)`。
- `trace_id` 走 ContextVar，RunSlot.claim 设置一次，整棵子任务树自动继承。
- 控制台人类可读；文件纯 JSONL（一行一个 JSON 对象），便于 jq / SIEM。
- 每个 trace 额外写一份 `logs/traces/{trace_id}.jsonl`，调完一键 `cat`。
- 保留老 facade（`logger.info(dict, trace_id)` 等），现有 ~840 处调用零改动。
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys as _sys
import threading
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，早期 import 链里就可能打中文日志，必须在装 handler 前切 UTF-8。
if _sys.platform == "win32":
    for _stream in (_sys.stdout, _sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "SYSTEM": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_trace_var: ContextVar[str] = ContextVar("log_trace_id", default="")
_config_lock = threading.Lock()
_configured = False
_per_trace_handler: "_PerTraceHandler | None" = None


# ── Formatters ────────────────────────────────────────────────────────────

def _json_safe(v: Any) -> Any:
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except TypeError:
        return str(v)


def _human_value(v: Any) -> str:
    if isinstance(v, str):
        return v.replace("\n", "\\n")
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


class _JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "msg": record.getMessage(),
        }
        tid = getattr(record, "trace_id", "") or ""
        if tid:
            obj["trace_id"] = tid
        structured = getattr(record, "structured", None)
        if isinstance(structured, dict) and structured:
            for k, v in structured.items():
                # 避免与顶层 key 冲突
                key = k if k not in obj else f"_{k}"
                obj[key] = _json_safe(v)
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class _HumanFormatter(logging.Formatter):
    _LEVEL_ABBR = {"DEBUG": "DBG", "INFO": "INF", "WARNING": "WRN", "ERROR": "ERR", "CRITICAL": "CRT"}

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S") + f".{int(record.msecs):03d}"
        lvl = self._LEVEL_ABBR.get(record.levelname, record.levelname[:3])
        loc = f"{record.module}:{record.lineno}"
        tid = getattr(record, "trace_id", "") or ""
        head = f"{ts} {lvl} {loc}"
        if tid:
            head += f" [{tid}]"
        parts: list[str] = []
        msg = record.getMessage()
        if msg:
            parts.append(msg)
        structured = getattr(record, "structured", None)
        if isinstance(structured, dict):
            for k, v in structured.items():
                parts.append(f"{k}={_human_value(v)}")
        line = head + (" | " + " | ".join(parts) if parts else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ── Filter：确保每条 record 上都有 trace_id / structured 字段 ───────────────

class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = _trace_var.get()
        if not hasattr(record, "structured"):
            record.structured = {}
        return True


# ── 每 trace 一个文件 ─────────────────────────────────────────────────────

def _safe_name(trace_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in trace_id)[:64] or "anon"


class _PerTraceHandler(logging.Handler):
    def __init__(self, directory: str | os.PathLike):
        super().__init__()
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, io.TextIOBase] = {}
        self._flock = threading.Lock()
        self.setFormatter(_JsonlFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        tid = getattr(record, "trace_id", "") or ""
        if not tid:
            return
        try:
            line = self.format(record)
            with self._flock:
                fp = self._files.get(tid)
                if fp is None:
                    fp = (self._dir / f"{_safe_name(tid)}.jsonl").open("a", encoding="utf-8")
                    self._files[tid] = fp
                fp.write(line + "\n")
                fp.flush()
        except Exception:
            self.handleError(record)

    def close_trace(self, trace_id: str) -> None:
        with self._flock:
            fp = self._files.pop(trace_id, None)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._flock:
            for fp in self._files.values():
                try:
                    fp.close()
                except Exception:
                    pass
            self._files.clear()
        super().close()


# ── configure ────────────────────────────────────────────────────────────

_DEFAULT_NOISY = {
    "urllib3": "WARNING",
    "httpx": "WARNING",
    "httpcore": "WARNING",
    "PIL": "WARNING",
    "selenium": "WARNING",
    "asyncio": "WARNING",
    "watchfiles": "WARNING",
}


def configure(
    *,
    level: str = "INFO",
    log_dir: str = "./logs",
    per_module_levels: dict[str, str] | None = None,
    per_trace_files: bool = True,
    backup_days: int = 7,
) -> None:
    """幂等地装配 root handlers。app.py 可在启动后用真实配置再调一次。"""
    global _configured, _per_trace_handler
    with _config_lock:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()

        # 清掉我们之前装过的 handlers（以 marker 区分第三方/用户的）
        for h in list(root.handlers):
            if getattr(h, "_rbagent_managed", False):
                root.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass

        # 同样只保留一个 _ContextFilter
        for f in list(root.filters):
            if isinstance(f, _ContextFilter):
                root.removeFilter(f)
        root.addFilter(_ContextFilter())

        root.setLevel(_LEVEL_MAP.get(level.upper(), logging.INFO))

        # 控制台
        ch = logging.StreamHandler()
        ch.setFormatter(_HumanFormatter())
        ch._rbagent_managed = True  # type: ignore[attr-defined]
        root.addHandler(ch)

        # 主文件：纯 JSONL
        fh = TimedRotatingFileHandler(
            os.path.join(log_dir, "app.jsonl"),
            when="midnight",
            interval=1,
            backupCount=backup_days,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonlFormatter())
        fh._rbagent_managed = True  # type: ignore[attr-defined]
        root.addHandler(fh)

        # 每 trace 一个文件
        if per_trace_files:
            _per_trace_handler = _PerTraceHandler(os.path.join(log_dir, "traces"))
            _per_trace_handler._rbagent_managed = True  # type: ignore[attr-defined]
            root.addHandler(_per_trace_handler)
        else:
            _per_trace_handler = None

        # 兼容老名字：my_logger 清空 handlers 让它 propagate 到 root
        bridge = logging.getLogger("my_logger")
        for h in list(bridge.handlers):
            bridge.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        bridge.propagate = True
        bridge.setLevel(logging.DEBUG)  # root 再统一过滤

        # 噪声抑制
        merged = {**_DEFAULT_NOISY, **(per_module_levels or {})}
        for name, lvl in merged.items():
            logging.getLogger(name).setLevel(_LEVEL_MAP.get(lvl.upper(), logging.INFO))

        _configured = True


# ── ContextVar helpers ───────────────────────────────────────────────────

def set_trace_id(trace_id: str):
    """压入 trace_id（asyncio 任务会继承）。返回 reset 用 token。"""
    return _trace_var.set(trace_id or "")


def reset_trace_id(token) -> None:
    try:
        _trace_var.reset(token)
    except ValueError:
        # token 不属于当前 context（例如在别的线程里 reset）；直接清空。
        _trace_var.set("")


def current_trace_id() -> str:
    return _trace_var.get()


def finalize_trace(trace_id: str) -> None:
    """RunSlot release 时调用：关闭该 trace 的文件句柄，防止 fd 泄漏。"""
    if _per_trace_handler is not None and trace_id:
        _per_trace_handler.close_trace(trace_id)


# ── Facade（老调用入口，保持签名稳定）─────────────────────────────────────

_BRIDGE_LOGGER = logging.getLogger("my_logger")


def _emit(level_str: str, message: Any, trace_id: str = "", *, exc_info: bool = False) -> None:
    if isinstance(message, dict):
        msg = str(message.get("msg") or message.get("message") or "")
        structured = {
            k: v for k, v in message.items()
            if k not in ("msg", "message", "trace_id")
        }
        if not trace_id and message.get("trace_id"):
            trace_id = str(message["trace_id"])
    elif isinstance(message, str):
        msg = message
        structured = {}
    else:
        raise ValueError("logger message 必须是 str 或 dict")

    tid = trace_id or _trace_var.get() or ""
    level_int = _LEVEL_MAP.get(level_str.upper(), logging.INFO)
    extra = {"trace_id": tid, "structured": structured}
    # stacklevel=3 让 record.pathname / lineno / funcName 指向 facade 的调用方，
    # 而不是 utils/logger.py 自己。
    _BRIDGE_LOGGER.log(level_int, msg, extra=extra, exc_info=exc_info, stacklevel=3)


def info(message, trace_id: str = "", **_kw) -> None:
    _emit("INFO", message, trace_id)


def warning(message, trace_id: str = "", **_kw) -> None:
    _emit("WARNING", message, trace_id)


def error(message, trace_id: str = "", **_kw) -> None:
    _emit("ERROR", message, trace_id)


def debug(message, trace_id: str = "", **_kw) -> None:
    _emit("DEBUG", message, trace_id)


def exception(message, trace_id: str = "", **_kw) -> None:
    _emit("ERROR", message, trace_id, exc_info=True)


def sys(message, **_kw) -> None:  # noqa: A001 — 兼容老调用：`logger.sys(...)`
    _emit("INFO", message, "")


def get_logger(name: str) -> logging.Logger:
    """新代码推荐入口：`log = get_logger(__name__); log.info("...", extra={"structured": {...}})`"""
    return logging.getLogger(name)


def get_time() -> str:
    """兼容老调用。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 老代码里 `from utils.logger import logger` 仍能拿到一个 stdlib Logger 句柄。
logger = _BRIDGE_LOGGER


# ── 默认装配，保证 import 即用（app.py 可再调 configure 覆盖）──
if not _configured:
    configure()
