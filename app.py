import asyncio
import os
import signal
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
            logger.warning({
                "msg": f"收到信号 {signum}，正在停止任务… 再按一次 Ctrl+C 立即强制退出",
            })
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

        # 3. 请求 supervisor 取消（非阻塞）
        try:
            container.supervisor.request_cancel("sigint")
        except Exception as e:
            try:
                logger.error({"msg": "请求 supervisor 取消失败", "error": str(e)})
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ────────────────────────────────────────────────
    config.validate()
    utils_init.init()
    container = init_container()
    container.supervisor.start_scheduler()
    _install_sigint_escalator(container)
    logger.info({"msg": "应用启动完成"})

    yield  # 应用运行期间

    # ── 关闭 ────────────────────────────────────────────────
    logger.info({"msg": "应用正在关闭"})
    try:
        await container.supervisor.shutdown()
    except Exception as e:
        logger.error({"msg": "Supervisor 关闭异常", "error": str(e)})

    # 关闭 Chrome 客户端
    try:
        from utils.init_functions.init_chrome_client import close_chrome_client
        close_chrome_client()
    except Exception as e:
        logger.error({"msg": "Chrome 客户端关闭异常", "error": str(e)})


app = FastAPI(lifespan=lifespan)

# CORS 中间件（开发期允许所有来源；生产应收敛 allow_origins）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常 handler：路由内未捕获异常统一返回结构化响应
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error({
        "msg": "未捕获异常",
        "path": str(request.url.path),
        "method": request.method,
        "error": str(exc),
        "type": type(exc).__name__,
    })
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": str(exc),
            "type": type(exc).__name__,
        },
    )


# 注册路由
app.include_router(agent_route.router, prefix="/api/v1", tags=["Agent"])
app.include_router(callback_route.router, prefix="/api/v1", tags=["Callback"])
app.include_router(usage_route.router, prefix="/api/v1", tags=["Usage"])


@app.get("/health", tags=["健康检查"])
async def health():
    return {"status": "ok"}
