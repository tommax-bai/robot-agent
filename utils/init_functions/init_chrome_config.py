"""
准备 Chrome 启动命令。

只杀**我们自己的** Chrome for Testing 实例（基于配置的 chrome_binary 路径或 debug_port），
绝不影响用户正在使用的常规 Chrome。
"""
from __future__ import annotations

import subprocess
import time

import config
import utils.logger as logger


def init_chrome_config():
    if config.system["system_info"] == "darwin":
        _kill_existing_debug_chrome()
        config.system["chrome"]["chrome_command"] = [
            config.system["chrome"]["chrome_binary"],
            f"--remote-debugging-port={config.system['chrome']['debug_port']}",
            "--remote-allow-origins=*",
        ]
        return

    if config.system["system_info"] == "win32":
        _kill_existing_debug_chrome_windows()
        config.system["chrome"]["chrome_command"] = [
            config.system["chrome"]["chrome_binary"],
            f"--remote-debugging-port={config.system['chrome']['debug_port']}",
            f"--user-data-dir={config.system['chrome']['profile_dir']}",
            "--remote-allow-origins=*",
        ]
        return

    raise RuntimeError(f"未支持的平台: {config.system['system_info']}")


def _kill_existing_debug_chrome() -> None:
    """
    macOS：精确杀掉 debug 端口绑定的 Chrome 进程。
    优先用 debug_port 匹配（最精确），fallback 到 chrome_binary 路径匹配。
    绝不使用 `pkill "Google Chrome"` —— 那会杀掉用户所有 Chrome 进程。
    """
    debug_port = config.system["chrome"]["debug_port"]
    chrome_binary = config.system["chrome"]["chrome_binary"]

    killed_any = False

    # 1. 优先：lsof 找出占用 debug 端口的 PID
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{debug_port}"],
            capture_output=True, text=True, timeout=3,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        for pid in pids:
            subprocess.run(["kill", "-9", pid], capture_output=True)
            killed_any = True
            logger.info({"msg": "已结束占用 debug 端口的进程", "pid": pid, "port": debug_port})
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 2. fallback：pgrep -f 精确匹配 chrome_binary 路径
    if not killed_any:
        try:
            result = subprocess.run(
                ["pgrep", "-f", chrome_binary],
                capture_output=True, text=True, timeout=3,
            )
            pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True)
                logger.info({"msg": "已结束 Chrome for Testing 进程", "pid": pid})
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    time.sleep(1)


def _kill_existing_debug_chrome_windows() -> None:
    """
    Windows：通过 netstat + tasklist 找到占用 debug 端口的 Chrome 进程。
    （未在生产环境验证，仅作为示例骨架）
    """
    debug_port = config.system["chrome"]["debug_port"]
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{debug_port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                logger.info({"msg": "已结束占用 debug 端口的进程", "pid": pid})
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    time.sleep(1)
