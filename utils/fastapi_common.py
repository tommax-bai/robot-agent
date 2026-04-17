"""
共享 FastAPI 中间件与全局异常处理器配置。
所有 app 入口（app.py / app_session.py / app_brain.py）统一调用，避免重复代码。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import utils.logger as logger


def setup_app_middleware(app: FastAPI) -> None:
    """Add standard CORS middleware and global exception handler."""
    # CORS 中间件（开发期允许所有来源；生产应收敛 allow_origins）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            {
                "msg": "未捕获异常",
                "path": str(request.url.path),
                "method": request.method,
                "error": str(exc),
                "type": type(exc).__name__,
            }
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(exc),
                "type": type(exc).__name__,
            },
        )
