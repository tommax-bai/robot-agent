"""Chrome 启动命令准备与旧调试进程清理。

只杀**我们自己的** Chrome for Testing 实例（基于 chrome_binary 路径或 debug_port），
绝不影响用户正在使用的常规 Chrome。
"""

from __future__ import annotations

import re
import subprocess
import time

import config
import utils.logger as logger


def prepare() -> None:
    sys_ = config.settings.system
    chrome = sys_.chrome
    if sys_.system_info == "darwin":
        _kill_existing_macos()
        chrome.chrome_command = [
            chrome.chrome_binary,
            f"--remote-debugging-port={chrome.debug_port}",
            "--remote-allow-origins=*",
        ]
        return

    if sys_.system_info == "win32":
        _kill_existing_windows()
        chrome.chrome_command = [
            chrome.chrome_binary,
            f"--remote-debugging-port={chrome.debug_port}",
            f"--user-data-dir={chrome.profile_dir}",
            "--remote-allow-origins=*",
        ]
        return

    raise RuntimeError(f"未支持的平台: {sys_.system_info}")


def _kill_existing_macos() -> None:
    """macOS：精确杀掉 debug 端口绑定的 Chrome 进程。

    优先用 debug_port 匹配（最精确），fallback 到 chrome_binary 路径匹配。
    绝不使用 `pkill "Google Chrome"` —— 那会杀掉用户所有 Chrome 进程。
    """
    chrome = config.settings.system.chrome
    debug_port = chrome.debug_port
    chrome_binary = chrome.chrome_binary

    killed_any = False

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{debug_port}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        for pid in pids:
            subprocess.run(["kill", "-9", pid], capture_output=True)
            killed_any = True
            logger.info({"msg": "已结束占用 debug 端口的进程", "pid": pid, "port": debug_port})
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not killed_any:
        try:
            result = subprocess.run(
                ["pgrep", "-f", chrome_binary],
                capture_output=True,
                text=True,
                timeout=3,
            )
            pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True)
                logger.info({"msg": "已结束 Chrome for Testing 进程", "pid": pid})
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    time.sleep(1)


def _kill_existing_windows() -> None:
    """Windows：netstat 找到占用 debug 端口的进程并 taskkill。"""
    debug_port = config.settings.system.chrome.debug_port
    # 行尾锚定避免本地化列头踩空；不传 `-p TCP`（部分本地化构建拒绝）
    pattern = re.compile(rf"\s:{debug_port}\s+\S+\s+LISTENING\s+(\d+)\s*$")
    killed: set[str] = set()
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            pid = match.group(1)
            if pid in killed:
                continue
            killed.add(pid)
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
            logger.info({"msg": "已结束占用 debug 端口的进程", "pid": pid, "port": debug_port})
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    time.sleep(1)
