import os
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ────────────────────────────────────────────────
    config.validate()
    utils_init.init()
    container = init_container()
    container.supervisor.start_scheduler()
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
