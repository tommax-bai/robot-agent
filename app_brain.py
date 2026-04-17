"""
Brain Service 入口（端口 6702）。

决策引擎：Supervisor / Operator / Planner / VisionActionStep 等。
通过 RemoteBackend 调用 Session Service 的截图/动作 API。

启动前需设置：
  export SESSION_SERVICE_URL=http://localhost:6710
  uvicorn app_brain:app --port 6702
"""

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
import utils.logger as logger
from api.v1.route import agent as agent_route
from api.v1.route import callback as callback_route
from api.v1.route import usage as usage_route
from runtime.container import init_container
from utils.fastapi_common import setup_app_middleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()

    session_url = config.agent["runtime"].get("session_service_url", "")
    if not session_url:
        raise RuntimeError(
            "app_brain.py 需要 SESSION_SERVICE_URL 环境变量。"
            "单进程模式请用 app.py。"
        )

    container = init_container()
    from runtime.log_bridge import attach as attach_log_bridge
    attach_log_bridge(container.event_bus)

    for worker in container.workers.list():
        worker.supervisor.start_scheduler()

    port = os.getenv("AGENT_HTTP_PORT", "6702")
    logger.info({
        "msg": "Brain Service 启动完成",
        "port": port,
        "session_service": session_url,
        "workers": [w.account_id for w in container.workers.list()],
    })

    yield

    logger.info({"msg": "Brain Service 正在关闭"})
    for worker in container.workers.list():
        try:
            await worker.supervisor.shutdown()
        except Exception as e:
            logger.error({"msg": "Supervisor 关闭异常", "account": worker.account_id, "error": str(e)})
    try:
        container.workers.shutdown_all()
    except Exception as e:
        logger.error({"msg": "WorkerPool 关闭异常", "error": str(e)})
    try:
        container.page_context_cache.shutdown()
    except Exception:
        pass
    try:
        container.behavior_summarizer.shutdown()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)
setup_app_middleware(app)

app.include_router(agent_route.router, prefix="/api/v1", tags=["Agent"])
app.include_router(callback_route.router, prefix="/api/v1", tags=["Callback"])
app.include_router(usage_route.router, prefix="/api/v1", tags=["Usage"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "brain"}
