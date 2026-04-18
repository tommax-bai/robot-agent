"""SIGINT/SIGTERM 升级处理：连按两次 Ctrl+C 必定退出。

- 第一次 Ctrl+C：
    1) 立即将 SIGINT/SIGTERM 恢复为系统默认处理（SIG_DFL）——这样
       第二次信号无需再经 Python 解释器，由内核直接终止进程，即便
       主线程此刻卡在 pyautogui / selenium / requests 等释放 GIL
       的同步 C 调用里，也一定能退出。
    2) 停止 Chrome 监控线程的自动重启逻辑，避免调用方杀掉 Chrome
       子进程后被监控线程复活。
    3) 通过 supervisor.request_stop 触发 cancel token。
    4) 用 loop.call_soon_threadsafe 调度一次 asyncio 任务取消，
       让 uvicorn 走正常 lifespan shutdown。

- 第二次 Ctrl+C：SIG_DFL 直接结束进程（kill -INT），其同进程组的
  Chrome 子进程也一并收到 SIGINT。

用 signal.signal 而非 loop.add_signal_handler，是因为后者依赖事件
循环 tick——主线程阻塞时两次信号都不会执行 Python 处理器。而
signal.signal 的 C 级处理器可以把 SIG_DFL 装回去，后续信号才真正
有办法打断卡死的进程。
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import TYPE_CHECKING

import utils.logger as logger

if TYPE_CHECKING:
    from runtime.host import Host


def install_sigint_escalator(host: "Host") -> None:
    """在当前运行事件循环上安装 SIGINT/SIGTERM 升级处理器。"""
    loop = asyncio.get_running_loop()
    state = {"fired": False}

    def _handler(signum, _frame):
        if state["fired"]:
            os._exit(130)
        state["fired"] = True

        # 1. 立刻把默认处理器装回去，第二次 Ctrl+C 由内核兜底强杀。
        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
        except Exception:
            pass

        try:
            logger.warning({"msg": f"收到信号 {signum}，正在停止任务… 再按一次 Ctrl+C 立即强制退出"})
        except Exception:
            pass

        # 2. 停止 Chrome 监控自动重启（同步调用，仅翻一个 flag）
        try:
            from bootstrap import chrome_client
            chrome_client.disable_auto_restart()
        except Exception as e:
            try:
                logger.error({"msg": "停用 Chrome 自动重启失败", "error": str(e)})
            except Exception:
                pass

        # 3. 请求所有 worker 的 supervisor 取消（非阻塞）
        try:
            for worker in host.list():
                if worker.supervisor is None:
                    continue
                try:
                    worker.supervisor.request_stop("sigint")
                except Exception as e:
                    logger.error({"msg": "请求 supervisor 取消失败",
                                  "account": worker.account_id, "error": str(e)})
        except Exception:
            pass

        # 4. 线程安全地调度 asyncio 任务取消，让 uvicorn 进 shutdown
        def _cancel_all_tasks() -> None:
            current = asyncio.current_task()
            for t in asyncio.all_tasks(loop):
                if t is not current:
                    t.cancel()

        try:
            loop.call_soon_threadsafe(_cancel_all_tasks)
        except Exception:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # 非主线程或平台不支持时静默跳过
            pass


__all__ = ["install_sigint_escalator"]
