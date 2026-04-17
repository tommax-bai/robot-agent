"""
Session Service 入口（端口 6710）。

管理云手机 session 生命周期，暴露截图/动作/委托 API 供 Brain Service 调用，
同时提供 Dashboard 和 Live View。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

IN_DOCKER = os.getenv("IN_DOCKER", "False").lower() == "true"
if not IN_DOCKER:
    env = os.getenv("APP_ENV", "dev")
    load_dotenv(dotenv_path=f".{env}.env")

import config
import utils.logger as logger
from api.v1.route import session_api
from runtime.container_session import init_session_container
from utils.fastapi_common import setup_app_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    container = init_session_container()
    from runtime.log_bridge import attach as attach_log_bridge
    attach_log_bridge(container.event_bus)

    port = os.getenv("SESSION_SERVICE_PORT", "6710")
    logger.info({
        "msg": "Session Service 启动完成",
        "port": port,
        "workers": [w.account_id for w in container.list()],
    })

    yield

    logger.info({"msg": "Session Service 正在关闭"})
    container.shutdown_all()


app = FastAPI(lifespan=lifespan)
setup_app_middleware(app)

app.include_router(session_api.router, prefix="/api/v1", tags=["Session"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "session"}


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard", status_code=307)


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse("static/dashboard.html", media_type="text/html")
