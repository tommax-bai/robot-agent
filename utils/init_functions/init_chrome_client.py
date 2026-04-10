from selenium import webdriver  
from selenium.webdriver.chrome.options import Options  
import subprocess  
import time  
import requests  
import os  
import threading  
from typing import Optional, Any, List, Dict  
  
import config  
import utils.logger as logger  
  
  
class ChromeClient:  
    def __init__(self):  
        self._driver: Optional[webdriver.Chrome] = None  
        self._lock = threading.Lock()  
        self._running = False  
        self._monitor_thread: Optional[threading.Thread] = None  
  
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
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json",  
                timeout=2  
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
            logger.error(f"获取标签页列表失败: {e}")  
            return []  
  
    def list_all_targets(self) -> List[Dict[str, Any]]:  
        """  
        获取所有目标（包括 page、iframe、worker 等）  
        """  
        try:  
            resp = requests.get(  
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json",  
                timeout=2  
            )  
            if resp.status_code == 200:  
                return resp.json()  
            return []  
        except Exception as e:  
            logger.error(f"获取目标列表失败: {e}")  
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
                timeout=2  
            )  
            if resp.status_code == 200:  
                # 重新连接到新标签页  
                with self._lock:  
                    self._driver = self._connect_chrome()  
                return True  
            return False  
        except Exception as e:  
            logger.error(f"切换标签页失败: {e}")  
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
  
    def new_tab(self, url: str = "about:blank") -> Optional[str]:  
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
                timeout=5  
            )  
            if resp.status_code == 200:  
                return resp.json().get("id")  
            return None  
        except Exception as e:  
            logger.error(f"打开新标签页失败: {e}")  
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
                timeout=2  
            )  
            return resp.status_code == 200  
        except Exception as e:  
            logger.error(f"关闭标签页失败: {e}")  
            return False  
  
    def get_current_tab(self) -> Optional[Dict[str, Any]]:  
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
  
        self._monitor_thread = threading.Thread(  
            target=self._monitor_loop,  
            daemon=True,  
            name="ChromeMonitor"  
        )  
        self._monitor_thread.start()  
  
    def _monitor_loop(self):  
        """后台监控循环"""  
        while self._running:  
            time.sleep(3)  
  
            if not self._running:  
                break  
  
            if not self._is_debug_port_active():  
                logger.info("[Chrome 监控] 检测到 Chrome 已关闭，正在重启...")  
                with self._lock:  
                    self._driver = None  
                    if self._ensure_connected():  
                        logger.info(f"[Chrome 监控] 重连成功: {self._driver.current_url}")  
                    else:  
                        logger.error("[Chrome 监控] 重连失败")  
  
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
    def current_url(self) -> Optional[str]:  
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
                f"http://{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}/json",  
                timeout=2  
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
        """启动 Chrome"""  
        try:  
            os.makedirs(config.system['chrome']['profile_dir'], exist_ok=True)  
            cmd = config.system['chrome']['chrome_command']  
            logger.info(f"正在启动 Chrome: {' '.join(cmd)}")
  
            kwargs = {  
                "stdout": subprocess.DEVNULL,  
                "stderr": subprocess.DEVNULL,  
            }  
  
            if config.system['system_info'] == "win32":  
                startupinfo = subprocess.STARTUPINFO()  
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  
                startupinfo.wShowWindow = subprocess.SW_HIDE  
                kwargs["startupinfo"] = startupinfo  
  
            subprocess.Popen(cmd, **kwargs)  
  
            # 增加等待时间，从 10秒 增加到 15秒
            for _ in range(30):  
                time.sleep(0.5)  
                if self._is_debug_port_active():  
                    logger.info("Chrome 调试端口已激活，等待 2 秒确保服务就绪...")
                    time.sleep(2) # 额外等待，确保 CDP 服务完全启动
                    return True  
            return False  
        except Exception as e:  
            logger.error(f"启动 Chrome 失败: {e}")  
            return False  
  
    def _connect_chrome(self) -> Optional[webdriver.Chrome]:  
        """连接 Chrome，增加重试机制以应对启动时的不稳定状态"""  
        max_retries = 3
        for i in range(max_retries):
            try:  
                options = Options()  
                options.add_experimental_option(  
                    "debuggerAddress",  
                    f"{config.system['chrome']['chrome_ip']}:{config.system['chrome']['debug_port']}"  
                )  
                options.add_argument("--log-level=3")  
      
                if config.system['system_info'] == "win32":  
                    service = webdriver.ChromeService(log_output=subprocess.DEVNULL)  
                else:  
                    service = webdriver.ChromeService(executable_path=config.system["chrome"]["chromedriver_path"], log_output=os.devnull)  
      
                driver = webdriver.Chrome(service=service, options=options)
                # 简单检查连接是否真的有效
                driver.current_url
                return driver
            except Exception as e:  
                logger.warning(f"第 {i+1} 次连接 Chrome 失败: {e}")
                if i < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.error(f"连接 Chrome 最终失败: {e}")  
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
        keywords = [  
            "return", "=", ";", "var ", "let ", "const ",  
            "if", "for", "while", "function", "class", "try"  
        ]  
        return any(kw in script for kw in keywords)  
  
  
# ==================== 全局单例 ====================  
  
_instance: Optional[ChromeClient] = None  
  
  
def init_chrome_client() -> ChromeClient:  
    """初始化 Chrome 客户端"""  
    global _instance  
    if _instance is not None:  
        return _instance  
  
    _instance = ChromeClient()  
    if not _instance.start():  
        _instance = None  
        raise RuntimeError("Chrome 启动失败")  
  
    logger.info("Chrome 客户端初始化成功")  
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
        logger.info("Chrome 客户端已关闭")  
