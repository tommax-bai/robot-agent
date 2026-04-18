"""唯一的进程入口。

拓扑由 AGENT_TOPOLOGY 环境变量决定（monolith / brain / session，默认 monolith）。
旧的 `app_brain.py` / `app_session.py` 已废弃：

    # 单进程（本地 Chrome 或单节点 AgentBay）
    uvicorn app:app --port 6702

    # 决策层（需要 SESSION_SERVICE_URL）
    AGENT_TOPOLOGY=brain SESSION_SERVICE_URL=... uvicorn app:app --port 6702

    # 云手机 session 管理节点
    AGENT_TOPOLOGY=session AGENTBAY_API_KEY=... uvicorn app:app --port 6710
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

# ── 加载 .env 必须在 import config 之前 ──────────────────────
IN_DOCKER = os.getenv("IN_DOCKER", "False").lower() == "true"
if not IN_DOCKER:
    _env = os.getenv("APP_ENV", "dev")
    load_dotenv(dotenv_path=f".{_env}.env")

import config  # noqa: E402
import utils.logger as logger  # noqa: E402
from api.v1 import register_routers  # noqa: E402
from runtime import Host, attach_log_bridge  # noqa: E402
from runtime.signals import install_sigint_escalator  # noqa: E402
from utils.banner import log_dashboard_urls  # noqa: E402
from utils.http import setup_middleware  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 启动 ────────────────────────────────────────────────
    config.validate()
    # 用显式参数重装一次日志（import 时已自动装过一次默认 handlers，此处覆盖）。
    logger.configure(
        level=os.getenv("LOG_LEVEL", "INFO"),
        log_dir=os.getenv("LOG_DIR", "./logs"),
        per_trace_files=os.getenv("LOG_PER_TRACE", "1") != "0",
    )

    tp = config.topology
    if tp.needs_local_chrome:
        import bootstrap
        bootstrap.boot()

    host = Host.boot(tp)
    attach_log_bridge(host.events)
    for worker in host.list():
        if worker.supervisor is not None:
            worker.supervisor.start()

    if tp.has_brain:
        # SIGINT 升级处理：只在决策进程里安装，避免 session-only 进程干扰。
        install_sigint_escalator(host)

    logger.info({
        "msg": "应用启动完成",
        "topology": tp.value,
        "workers": [w.account_id for w in host.list()],
    })
    log_dashboard_urls(tp.value)

    yield

    # ── 关闭 ────────────────────────────────────────────────
    logger.info({"msg": "应用正在关闭"})

    for worker in host.list():
        if worker.supervisor is None:
            continue
        try:
            await worker.supervisor.stop()
        except Exception as e:
            logger.error({"msg": "Supervisor 关闭异常",
                          "account": worker.account_id, "error": str(e)})

    try:
        host.shutdown_all()
    except Exception as e:
        logger.error({"msg": "Host 关闭异常", "error": str(e)})

    if host.page_context_cache is not None:
        try:
            host.page_context_cache.shutdown()
        except Exception as e:
            logger.error({"msg": "页面分类后台任务关闭异常", "error": str(e)})

    if host.behavior_summarizer is not None:
        try:
            host.behavior_summarizer.shutdown()
        except Exception as e:
            logger.error({"msg": "行为总结后台任务关闭异常", "error": str(e)})

    if tp.needs_local_chrome:
        try:
            from bootstrap import chrome_client
            chrome_client.close()
        except Exception as e:
            logger.error({"msg": "Chrome 客户端关闭异常", "error": str(e)})


app = FastAPI(lifespan=lifespan, title="robot-agent")
setup_middleware(app)
register_routers(app, config.topology)


@app.get("/health", tags=["健康检查"])
async def health():
    return {"status": "ok", "topology": config.topology.value}


if config.topology.serves_dashboard:

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/dashboard", status_code=307)

    @app.get("/dashboard", tags=["Dashboard"], include_in_schema=False)
    async def dashboard():
        """单页监控面板：状态总览 + 云手机实时画面 + 操作按钮 + SSE 事件流。"""
        return FileResponse("static/dashboard.html", media_type="text/html")
