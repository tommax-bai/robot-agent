from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any

# 获取当前日期并格式化为字符串
current_date = datetime.now().strftime("%Y-%m-%d")
log_directory = "./logs/"
log_filename = f"{log_directory}app_log.log"

if not os.path.exists(log_directory):
    os.makedirs(log_directory)


# 创建日志记录器
logger = logging.getLogger("my_logger")
logger.setLevel(logging.INFO)
logger.propagate = False

if logger.handlers:
    logger.handlers.clear()


class JsonFileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        else:
            message = record.getMessage()
        return f"{self.formatTime(record, self.datefmt)} | {record.levelname} | {message}"


class HumanConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", None)
        if isinstance(payload, dict):
            message = _format_human_payload(payload)
        else:
            message = record.getMessage()
        return f"{self.formatTime(record, self.datefmt)} | {record.levelname} | {message}"


# 创建轮转文件处理器
handler = TimedRotatingFileHandler(log_filename, when="midnight", interval=1, backupCount=7, encoding="utf-8")
handler.setLevel(logging.INFO)

# 创建日志格式
file_formatter = JsonFileFormatter(datefmt="%Y-%m-%d %H:%M:%S")
console_formatter = HumanConsoleFormatter(datefmt="%Y-%m-%d %H:%M:%S")
handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(console_formatter)

# 将处理器添加到记录器
logger.addHandler(handler)
logger.addHandler(console_handler)


def get_time():
    """获取当前时间的字符串表示"""
    current_time = datetime.now()
    return current_time.strftime("%Y-%m-%d %H:%M:%S")


def log(level: str, message, trace_id: str = "", *args, **kwargs):
    """通用日志记录方法"""
    if isinstance(message, dict):
        payload = dict(message)
    elif isinstance(message, str):
        payload = {"msg": message}
    else:
        raise ValueError("logger.py -> log方法中的message参数必须是字符串或字典")

    if trace_id:
        payload["trace_id"] = trace_id

    extra = kwargs.pop("extra", {})
    extra = {**extra, "payload": payload}

    # 记录日志
    level_upper = level.upper()
    if level_upper in ("INFO", "SYSTEM"):
        logger.info("", *args, extra=extra, **kwargs)
    elif level_upper == "ERROR":
        logger.error("", *args, extra=extra, **kwargs)
    elif level_upper == "WARNING":
        logger.warning("", *args, extra=extra, **kwargs)
    elif level_upper == "DEBUG":
        logger.debug("", *args, extra=extra, **kwargs)
    else:
        raise ValueError(f"logger.py -> log方法中的level参数无效: {level}")


def _format_human_payload(payload: dict[str, Any]) -> str:
    trace_id = payload.get("trace_id")
    msg = payload.get("msg") or payload.get("message") or ""
    parts = [str(msg)] if msg else []

    for key, value in payload.items():
        if key in ("msg", "message", "trace_id"):
            continue
        parts.append(f"{key}={_format_human_value(value)}")

    text = " | ".join(parts) if parts else _format_human_value(payload)
    if trace_id:
        return f"[{trace_id}] {text}"
    return text


def _format_human_value(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("\n", "\\n")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def info(message, trace_id: str = "", *args, **kwargs):
    """记录 INFO 级别的日志"""
    log("INFO", message, trace_id, *args, **kwargs)


def error(message, trace_id: str = "", *args, **kwargs):
    """记录 ERROR 级别的日志"""
    log("ERROR", message, trace_id, *args, **kwargs)


def warning(message, trace_id: str = "", *args, **kwargs):
    """记录 WARNING 级别的日志"""
    log("WARNING", message, trace_id, *args, **kwargs)


def debug(message, trace_id: str = "", *args, **kwargs):
    """记录 DEBUG 级别的日志"""
    log("DEBUG", message, trace_id, *args, **kwargs)


def sys(message, *args, **kwargs):
    """记录 SYSTEM 级别的日志（无需 trace_id）"""
    log("SYSTEM", message, "", *args, **kwargs)
