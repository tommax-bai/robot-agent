import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

IN_DOCKER = os.getenv('IN_DOCKER', 'False').lower() == 'true'
if not IN_DOCKER:
    env = os.getenv('APP_ENV', 'dev')
    env_file = f".{env}.env"
    load_dotenv(dotenv_path=env_file)

import utils.init_functions.init as utils_init
from api.v1.route import agent as agent_route
from api.v1.route import callback as callback_route
from api.v1.route import usage as usage_route
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 应用启动时执行的操作
    utils_init.init()
    
    # 启动 Supervisor 的养号循环
    from agents.supervisor import supervisor
    supervisor.start_scheduler()

    yield  # 在这里 yield 表示应用正在运行

app = FastAPI(lifespan=lifespan)

# 注册路由
app.include_router(agent_route.router, prefix="/api/v1", tags=["Agent"])
app.include_router(callback_route.router, prefix="/api/v1", tags=["Callback"])
app.include_router(usage_route.router, prefix="/api/v1", tags=["Usage"])
@app.get("/health", tags=["健康检查"])
async def health():
    return {"status": "ok"}
    
