import sys
import config
import utils.logger as logger

def init_system_info():
    system_info = sys.platform
    config.global_config["system_info"] = system_info
    logger.sys(f"系统信息: {system_info}")
    if system_info not in ["win32", "darwin"]:
        raise ValueError(f"系统信息: {system_info} 不支持")
