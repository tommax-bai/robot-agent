from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any, Dict, List

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

import config
import utils.logger as logger


class ChromeClient:
    def __init__(self):
        self._driver: webdriver.Chrome | None = None
        self._lock = threading.Lock()
        self._running = False
        self._monitor_thread: threading.Thread | None = None

    # ==================== Tab 管理功能 ====================

    def list_tabs(self) -> List[Dict[str, Any]]:
        """
        获取所有标签页列表

        Returns:
            [
                {
                    "id": "xxx",
                    "title": "Google",
                    "url": "https://www.google.com",
                    "type": "page"
                },
                ...
            ]
        """
        try:
            resp = requests.get(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json", timeout=2
            )
            if resp.status_code == 200:
                tabs = resp.json()
                return [
                    {
                        "id": tab.get("id"),
                        "title": tab.get("title", ""),
                        "url": tab.get("url", ""),
                        "type": tab.get("type", ""),
                    }
                    for tab in tabs
                    if tab.get("type") == "page"  # 只返回页面类型
                ]
            return []
        except Exception as e:
            logger.error({"msg": "获取标签页列表失败", "error": str(e)})
            return []

    def list_all_targets(self) -> List[Dict[str, Any]]:
        """
        获取所有目标（包括 page、iframe、worker 等）
        """
        try:
            resp = requests.get(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json", timeout=2
            )
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.error({"msg": "获取目标列表失败", "error": str(e)})
            return []

    def get_tab_count(self) -> int:
        """获取标签页数量"""
        return len(self.list_tabs())

    def switch_to_tab(self, tab_id: str) -> bool:
        """
        切换到指定标签页

        Args:
            tab_id: 标签页 ID（从 list_tabs 获取）
        """
        try:
            # 通过 CDP 激活标签页
            resp = requests.get(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json/activate/{tab_id}",
                timeout=2,
            )
            if resp.status_code == 200:
                # 重新连接到新标签页
                with self._lock:
                    self._driver = self._connect_chrome()
                return True
            return False
        except Exception as e:
            logger.error({"msg": "切换标签页失败", "error": str(e)})
            return False

    def switch_to_tab_by_url(self, url_pattern: str) -> bool:
        """
        通过 URL 匹配切换标签页

        Args:
            url_pattern: URL 包含的字符串
        """
        tabs = self.list_tabs()
        for tab in tabs:
            if url_pattern in tab["url"]:
                return self.switch_to_tab(tab["id"])
        return False

    def switch_to_tab_by_title(self, title_pattern: str) -> bool:
        """
        通过标题匹配切换标签页

        Args:
            title_pattern: 标题包含的字符串
        """
        tabs = self.list_tabs()
        for tab in tabs:
            if title_pattern in tab["title"]:
                return self.switch_to_tab(tab["id"])
        return False

    def new_tab(self, url: str = "about:blank") -> str | None:
        """
        打开新标签页

        Args:
            url: 新标签页的 URL

        Returns:
            新标签页的 ID
        """
        try:
            resp = requests.put(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json/new?{url}",
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            return None
        except Exception as e:
            logger.error({"msg": "打开新标签页失败", "error": str(e)})
            return None

    def close_tab(self, tab_id: str) -> bool:
        """
        关闭指定标签页

        Args:
            tab_id: 标签页 ID
        """
        try:
            resp = requests.get(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json/close/{tab_id}",
                timeout=2,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.error({"msg": "关闭标签页失败", "error": str(e)})
            return False

    def get_current_tab(self) -> Dict[str, Any] | None:
        """获取当前活动标签页信息"""
        with self._lock:
            if not self._driver:
                return None
            try:
                current_url = self._driver.current_url
                tabs = self.list_tabs()
                for tab in tabs:
                    if tab["url"] == current_url:
                        return tab
                return None
            except Exception:
                return None

    def get_window_bounds(self) -> Dict[str, int] | None:
        """
        通过 CDP 获取本项目连接的 Chrome 窗口 bounds（left/top/width/height，单位 DIP）。

        这是"本项目启动的 Chrome"的权威来源：debugger 只会附加到我们自己启动
        的 Chrome 实例，所以 Browser.getWindowForTarget 返回的窗口就是项目窗口。
        多开 Chrome 场景下由截图工具用它来筛出正确的 pywinctl 窗口。

        Returns:
            {"left": int, "top": int, "width": int, "height": int} 或 None
        """
        with self._lock:
            if not self._check_driver_alive():
                return None
            try:
                result = self._driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
                bounds = (result or {}).get("bounds") or {}
                if "left" not in bounds or "top" not in bounds:
                    return None
                return {
                    "left": int(bounds.get("left", 0)),
                    "top": int(bounds.get("top", 0)),
                    "width": int(bounds.get("width", 0)),
                    "height": int(bounds.get("height", 0)),
                }
            except Exception as e:
                logger.warning({"msg": "获取 Chrome 窗口 bounds 失败", "error": str(e)})
                return None

    # ==================== 原有方法 ====================

    def start(self) -> bool:
        """启动并连接 Chrome"""
        self._running = True
        with self._lock:
            connected = self._ensure_connected()

        if connected:
            self._start_monitor()

        return connected

    def stop(self):
        """停止连接"""
        self._running = False

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)

        with self._lock:
            if self._driver:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None

    def _start_monitor(self):
        """启动后台监控线程"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="ChromeMonitor")
        self._monitor_thread.start()

    def _monitor_loop(self):
        """后台监控循环"""
        while self._running:
            time.sleep(3)

            if not self._running:
                break

            if not self._is_debug_port_active():
                logger.info({"msg": "[Chrome 监控] 检测到 Chrome 已关闭，正在重启..."})
                with self._lock:
                    self._driver = None
                    if self._ensure_connected():
                        logger.info({"msg": "[Chrome 监控] 重连成功", "url": self._driver.current_url})
                    else:
                        logger.error({"msg": "[Chrome 监控] 重连失败"})

    def execute_js(self, script: str, auto_return: bool = True) -> Any:
        """执行 JavaScript"""
        with self._lock:
            if not self._ensure_connected():
                raise ConnectionError("无法连接到 Chrome")

            if auto_return and not self._has_statement(script):
                script = f"return {script}"

            try:
                return self._driver.execute_script(script)
            except Exception as e:
                self._driver = None
                if self._ensure_connected():
                    return self._driver.execute_script(script)
                raise RuntimeError(f"JS 执行失败: {e}")

    def execute_async_js(self, script: str, timeout: int = 30) -> Any:
        """执行异步 JavaScript"""
        with self._lock:
            if not self._ensure_connected():
                raise ConnectionError("无法连接到 Chrome")
            self._driver.set_script_timeout(timeout)
            return self._driver.execute_async_script(script)

    @property
    def current_url(self) -> str | None:
        """当前 URL"""
        with self._lock:
            if self._driver:
                try:
                    return self._driver.current_url
                except Exception:
                    return None
        return None

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        with self._lock:
            return self._check_driver_alive()

    def _is_debug_port_active(self) -> bool:
        """检查调试端口"""
        try:
            resp = requests.get(
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json", timeout=2
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _check_driver_alive(self) -> bool:
        """检查 driver 是否有效"""
        if not self._driver:
            return False
        try:
            self._driver.current_url
            return True
        except Exception:
            return False

    def _start_chrome(self) -> bool:
        """启动 Chrome 并轮询 debug port 直到就绪"""
        try:
            os.makedirs(config.system["chrome"]["profile_dir"], exist_ok=True)
            cmd = config.system["chrome"]["chrome_command"]

            # 预检查：binary 是否存在
            chrome_binary = cmd[0] if cmd else None
            if not chrome_binary or not os.path.exists(chrome_binary):
                logger.error(
                    {
                        "msg": "Chrome 可执行文件不存在",
                        "path": chrome_binary,
                        "hint": "请下载 Chrome for Testing 或设置 CHROME_BINARY 环境变量",
                    }
                )
                return False

            # chromedriver 同样预检查（_connect_chrome 阶段会用）
            chromedriver_path = config.system["chrome"].get("chromedriver_path")
            if chromedriver_path and not os.path.exists(chromedriver_path):
                logger.error(
                    {
                        "msg": "ChromeDriver 不存在",
                        "path": chromedriver_path,
                        "hint": "请下载与 Chrome 同版本的 chromedriver 或设置 CHROMEDRIVER_PATH 环境变量",
                    }
                )
                return False

            logger.info({"msg": "正在启动 Chrome", "cmd": " ".join(cmd)})

            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if config.system["system_info"] == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = startupinfo

            proc = subprocess.Popen(cmd, **kwargs)

            # 轮询 debug port，同时检测子进程是否已退出
            for _ in range(30):
                time.sleep(0.5)
                # 检测子进程是否还活着（None 表示运行中）
                if proc.poll() is not None:
                    logger.error(
                        {
                            "msg": "Chrome 子进程意外退出",
                            "exit_code": proc.returncode,
                            "cmd": cmd,
                        }
                    )
                    return False
                if self._is_debug_port_active():
                    logger.info({"msg": "Chrome 调试端口已激活，等待 2 秒确保服务就绪..."})
                    time.sleep(2)
                    return True

            logger.error(
                {
                    "msg": "Chrome 启动超时（15s 内 debug port 未激活）",
                    "debug_port": config.system["chrome"]["debug_port"],
                    "cmd": cmd,
                }
            )
            return False
        except Exception as e:
            logger.error({"msg": "启动 Chrome 失败", "error": str(e)})
            return False

    def _connect_chrome(self) -> webdriver.Chrome | None:
        """连接 Chrome，增加重试机制以应对启动时的不稳定状态"""
        max_retries = 3
        for i in range(max_retries):
            try:
                options = Options()
                options.add_experimental_option(
                    "debuggerAddress", f"{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}"
                )
                options.add_argument("--log-level=3")

                if config.system["system_info"] == "win32":
                    service = webdriver.ChromeService(log_output=subprocess.DEVNULL)
                else:
                    service = webdriver.ChromeService(
                        executable_path=config.system["chrome"]["chromedriver_path"], log_output=os.devnull
                    )

                driver = webdriver.Chrome(service=service, options=options)
                # 简单检查连接是否真的有效
                driver.current_url
                return driver
            except Exception as e:
                logger.warning({"msg": f"第 {i + 1} 次连接 Chrome 失败", "error": str(e)})
                if i < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.error({"msg": "连接 Chrome 最终失败", "error": str(e)})
                    return None

    def _ensure_connected(self) -> bool:
        """确保已连接"""
        if self._check_driver_alive():
            return True
        if not self._is_debug_port_active():
            if not self._start_chrome():
                return False
        self._driver = self._connect_chrome()
        return self._driver is not None

    @staticmethod
    def _has_statement(script: str) -> bool:
        """判断是否包含语句"""
        keywords = ["return", "=", ";", "var ", "let ", "const ", "if", "for", "while", "function", "class", "try"]
        return any(kw in script for kw in keywords)


# ==================== 全局单例 ====================

_instance: ChromeClient | None = None


def init_chrome_client() -> ChromeClient:
    """初始化 Chrome 客户端"""
    global _instance
    if _instance is not None:
        return _instance

    _instance = ChromeClient()
    if not _instance.start():
        _instance = None
        raise RuntimeError(
            "Chrome 启动失败 — 请查看上方 logger 输出定位根因。"
            "常见原因：(1) chrome_binary 路径不存在 (2) chromedriver 路径不存在 "
            "(3) debug port 被占用 (4) 子进程立即崩溃。"
            "可通过环境变量 CHROME_BINARY / CHROMEDRIVER_PATH 覆盖默认路径。"
        )

    logger.info({"msg": "Chrome 客户端初始化成功"})
    return _instance


def get_chrome() -> ChromeClient:
    """获取实例"""
    if _instance is None:
        raise RuntimeError("请先调用 init_chrome_client()")
    return _instance


def close_chrome_client():
    """关闭"""
    global _instance
    if _instance:
        _instance.stop()
        _instance = None
        logger.info({"msg": "Chrome 客户端已关闭"})


def disable_chrome_auto_restart() -> None:
    """快速停止 Chrome 监控线程的"自动重启 Chrome"行为。

    用于关闭流程：当 SIGINT 把 Chrome 子进程一并杀掉后，监控线程会在 3 秒
    内检测到 debug port 失效并立即重启 Chrome——这会让用户以为关不掉。
    在 supervisor.shutdown 之前调一下这个函数，让监控循环立即退出，避免
    与关闭流程赛跑。本函数不会真正 quit driver，由 close_chrome_client 完成。
    """
    if _instance is not None:
        _instance._running = False
