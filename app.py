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


_sigint_count = 0


def _install_sigint_escalator(container) -> None:
    """注册 SIGINT/SIGTERM 升级处理：
       - 第一次 Ctrl+C：立即停止 Chrome 监控自动重启 + 取消 supervisor 当前任务 +
         取消所有 asyncio 任务，让 uvicorn 退出 lifespan 走正常关闭流程。
       - 第二次 Ctrl+C：os._exit(130) 强制退出，绕过任何卡死在同步代码
         （pyautogui / requests.post LLM / Selenium 等）的协程。
    """
    loop = asyncio.get_running_loop()

    def _handler(signame: str) -> None:
        global _sigint_count
        _sigint_count += 1
        if _sigint_count == 1:
            logger.warning({
                "msg": f"收到 {signame}，正在停止任务…再按一次 Ctrl+C 强制退出",
            })
            # 1. 立即停止 Chrome 监控线程的自动重启逻辑
            try:
                from utils.init_functions.init_chrome_client import disable_chrome_auto_restart
                disable_chrome_auto_restart()
            except Exception as e:
                logger.error({"msg": "停用 Chrome 自动重启失败", "error": str(e)})
            # 2. 让 supervisor 的 cancel token 立刻翻牌
            try:
                container.supervisor.request_cancel("sigint")
            except Exception as e:
                logger.error({"msg": "请求 supervisor 取消失败", "error": str(e)})
            # 3. 取消所有 asyncio 任务，触发 uvicorn 进入 shutdown
            current = asyncio.current_task()
            for t in asyncio.all_tasks(loop):
                if t is not current:
                    t.cancel()
        else:
            logger.error({"msg": f"再次收到 {signame}，强制退出进程"})
            os._exit(130)

    for sig, name in ((signal.SIGINT, "SIGINT"), (signal.SIGTERM, "SIGTERM")):
        try:
            loop.add_signal_handler(sig, _handler, name)
        except (NotImplementedError, RuntimeError):
            # Windows 上 add_signal_handler 不支持，回退到 signal.signal
            signal.signal(sig, lambda *_args, _n=name: _handler(_n))


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
