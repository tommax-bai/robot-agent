from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

IN_DOCKER = os.getenv("IN_DOCKER", "False").lower() == "true"
if not IN_DOCKER:
    env = os.getenv("APP_ENV", "dev")
    load_dotenv(dotenv_path=f".{env}.env")

import config
import utils.init_functions.init as utils_init
import utils.logger as logger
from api.v1.route import agent as agent_route
from api.v1.route import callback as callback_route
from api.v1.route import usage as usage_route
from runtime.container import init_container
from utils.fastapi_common import setup_app_middleware


def _install_sigint_escalator(container) -> None:
    """注册 SIGINT/SIGTERM 升级处理（连按两次 Ctrl+C 必定退出）：

    - 第一次 Ctrl+C：
        1) 立即将 SIGINT/SIGTERM 恢复为系统默认处理（SIG_DFL）——这样
           第二次信号无需再经 Python 解释器，由内核直接终止进程，即便
           主线程此刻卡在 pyautogui / selenium / requests 等释放 GIL
           的同步 C 调用里，也一定能退出。
        2) 停止 Chrome 监控线程的自动重启逻辑，避免调用方杀掉 Chrome
           子进程后被监控线程复活。
        3) 通过 supervisor.request_cancel 触发 cancel token。
        4) 用 loop.call_soon_threadsafe 调度一次 asyncio 任务取消，
           让 uvicorn 走正常 lifespan shutdown。

    - 第二次 Ctrl+C：SIG_DFL 直接结束进程（kill -INT），其同进程组的
      Chrome 子进程也一并收到 SIGINT。

    用 signal.signal 而非 loop.add_signal_handler，是因为后者依赖事件
    循环 tick——主线程阻塞时两次信号都不会执行 Python 处理器。而
    signal.signal 的 C 级处理器可以把 SIG_DFL 装回去，后续信号才真正
    有办法打断卡死的进程。
    """
    loop = asyncio.get_running_loop()
    state = {"fired": False}

    def _handler(signum, _frame):
        if state["fired"]:
            # 理论上不会走到：SIG_DFL 已经接管了后续信号。
            os._exit(130)
        state["fired"] = True

        # 1. 立刻把默认处理器装回去，第二次 Ctrl+C 由内核兜底强杀。
        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
        except Exception:
            pass

        try:
            logger.warning(
                {
                    "msg": f"收到信号 {signum}，正在停止任务… 再按一次 Ctrl+C 立即强制退出",
                }
            )
        except Exception:
            pass

        # 2. 停止 Chrome 监控自动重启（同步调用，仅翻一个 flag）
        try:
            from utils.init_functions.init_chrome_client import disable_chrome_auto_restart

            disable_chrome_auto_restart()
        except Exception as e:
            try:
                logger.error({"msg": "停用 Chrome 自动重启失败", "error": str(e)})
            except Exception:
                pass

        # 3. 请求所有 worker 的 supervisor 取消（非阻塞）
        try:
            for worker in container.workers.list():
                try:
                    worker.supervisor.request_cancel("sigint")
                except Exception as e:
                    logger.error({"msg": "请求 supervisor 取消失败", "account": worker.account_id, "error": str(e)})
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


def _detect_lan_ips() -> list[str]:
    """枚举本机所有非 loopback 的 IPv4 地址（跨 Mac/Linux 通用，不依赖 hostname 解析）。"""
    import socket

    ips: set[str] = set()
    # 主路由 IP（指向默认网关那张网卡上的 IP，最常用的 LAN 地址）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 不真正发包，只让内核选一个本地地址
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    # 全部网卡 IP（兜底，覆盖多网段）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if "." in ip and not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def _log_dashboard_urls() -> None:
    """启动 banner：把 dashboard / API 的可访问地址写进日志，方便复制。

    端口从 AGENT_HTTP_PORT 读取（与启动 uvicorn 时的 --port 保持一致）。
    """
    port = os.getenv("AGENT_HTTP_PORT", "6702")
    lines = [
        "═══════════════════════════════════════════════════════",
        f"  📊 Dashboard      http://127.0.0.1:{port}/dashboard",
    ]
    for ip in _detect_lan_ips():
        lines.append(f"  🌐 局域网访问     http://{ip}:{port}/dashboard")
    lines.extend(
        [
            f"  ⚙️  API 根路径     http://127.0.0.1:{port}/api/v1",
            "  ⚠️  当前 API 无认证；开 0.0.0.0 + LAN = 网内任何人都能控制 agent",
            "═══════════════════════════════════════════════════════",
        ]
    )
    for line in lines:
        logger.info({"msg": line})


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ────────────────────────────────────────────────
    config.validate()
    utils_init.init()
    container = init_container()
    # 把 utils.logger 的记录桥接到 event_bus，dashboard SSE 可订阅
    from runtime.log_bridge import attach as attach_log_bridge

    attach_log_bridge(container.event_bus)
    # 启动所有 worker 的调度循环（每个 worker 独立 supervisor，互不干扰）
    for worker in container.workers.list():
        worker.supervisor.start_scheduler()
    _install_sigint_escalator(container)
    logger.info({"msg": "应用启动完成", "workers": [w.account_id for w in container.workers.list()]})
    _log_dashboard_urls()

    yield  # 应用运行期间

    # ── 关闭 ────────────────────────────────────────────────
    logger.info({"msg": "应用正在关闭"})
    # 1. 先停所有 worker 的 supervisor（取消任务、停调度循环）
    for worker in container.workers.list():
        try:
            await worker.supervisor.shutdown()
        except Exception as e:
            logger.error({"msg": "Supervisor 关闭异常", "account": worker.account_id, "error": str(e)})
    # 2. 释放云端 session（AgentBay backend.teardown）
    try:
        container.workers.shutdown_all()
    except Exception as e:
        logger.error({"msg": "WorkerPool 关闭异常", "error": str(e)})

    try:
        container.page_context_cache.shutdown()
    except Exception as e:
        logger.error({"msg": "页面分类后台任务关闭异常", "error": str(e)})

    try:
        container.behavior_summarizer.shutdown()
    except Exception as e:
        logger.error({"msg": "行为总结后台任务关闭异常", "error": str(e)})

    # 关闭 Chrome 客户端
    try:
        from utils.init_functions.init_chrome_client import close_chrome_client

        close_chrome_client()
    except Exception as e:
        logger.error({"msg": "Chrome 客户端关闭异常", "error": str(e)})


app = FastAPI(lifespan=lifespan)
setup_app_middleware(app)

# 注册路由
app.include_router(agent_route.router, prefix="/api/v1", tags=["Agent"])
app.include_router(callback_route.router, prefix="/api/v1", tags=["Callback"])
app.include_router(usage_route.router, prefix="/api/v1", tags=["Usage"])


@app.get("/health", tags=["健康检查"])
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root():
    """根路径默认跳转到监控看板。"""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/dashboard", status_code=307)


@app.get("/dashboard", tags=["Dashboard"], include_in_schema=False)
async def dashboard():
    """单页监控面板：状态总览 + 云手机实时画面 + 操作按钮 + SSE 事件流。

    HTML 文件在 static/dashboard.html，所有数据通过 /api/v1/agent/* 接口拉取。
    """
    from fastapi.responses import FileResponse

    return FileResponse("static/dashboard.html", media_type="text/html")
