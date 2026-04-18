"""FastAPI 公共配置：CORS 中间件 + 全局异常处理器。"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import utils.logger as logger


def setup_middleware(app: FastAPI) -> None:
    """挂载 CORS + 全局异常处理器。开发期 CORS 放开，生产应收敛 allow_origins。"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def _handler(request: Request, exc: Exception):
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
            content={"ok": False, "error": str(exc), "type": type(exc).__name__},
        )
