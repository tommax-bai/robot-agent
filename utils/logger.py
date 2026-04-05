import logging
import json
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime

# 获取当前日期并格式化为字符串
current_date = datetime.now().strftime("%Y-%m-%d")
log_directory = "./logs/"
log_filename = f'{log_directory}app_log.log'

if not os.path.exists(log_directory):
    os.makedirs(log_directory)


# 创建日志记录器
logger = logging.getLogger("my_logger")
logger.setLevel(logging.INFO)

# 创建轮转文件处理器
handler = TimedRotatingFileHandler(
    log_filename, 
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8"
    )
handler.setLevel(logging.INFO)

# 创建日志格式
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# 将处理器添加到记录器
logger.addHandler(handler)
logger.addHandler(console_handler)
def get_time():
    """获取当前时间的字符串表示"""
    current_time = datetime.now()
    return current_time.strftime("%Y-%m-%d %H:%M:%S")

def log(level: str, message, trace_id: str = "", *args, **kwargs):
    """通用日志记录方法"""
    # 如果 message 是字典类型，转换为 JSON 字符串
    if isinstance(message, dict):
        message = json.dumps(message, ensure_ascii=False, indent=4)
    elif not isinstance(message, str):
        raise ValueError("logger.py -> log方法中的message参数必须是字符串或字典")

    # 构造日志消息
    json_message = {
        "time": get_time(),
        "message": message,
    }
    # 只有 trace_id 非空时才加入
    if trace_id:
        json_message["trace_id"] = trace_id

    log_msg = json.dumps(json_message, ensure_ascii=False, indent=4)
    
    # 记录日志
    level_upper = level.upper()
    if level_upper == "INFO" or level_upper == "SYSTEM":
        logger.info(log_msg, *args, **kwargs)
    elif level_upper == "ERROR":
        logger.error(log_msg, *args, **kwargs)
    elif level_upper == "WARNING":
        logger.warning(log_msg, *args, **kwargs)
    elif level_upper == "DEBUG":
        logger.debug(log_msg, *args, **kwargs)
    elif level_upper == "SYSTEM":
        logger.critical(log_msg, *args, **kwargs)
    else:
        raise ValueError(f"logger.py -> log方法中的level参数无效: {level}")

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