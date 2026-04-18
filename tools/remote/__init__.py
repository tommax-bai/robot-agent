"""
远程 HTTP 执行环境：Brain Service 通过 HTTP 调用 Session Service。

两个对象：
- RemoteEnv: 实现 Environment 协议，把 capture/perform 代理到远程 /session/*
- RemoteDelegateStrategy: agentbay 模式下的 subtask strategy，发 /session/delegate-task
  让 Session Service 跑 mobile_use Agent。它是 strategy 不是 env，之前错放在 backends/。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

import utils.logger as logger
from agents.base import AgentError, TaskResult

if TYPE_CHECKING:
    from agents.base import SubTask
    from runtime.ctx import RunContext


class RemoteEnv:
    """Environment over HTTP — 调用 Session Service 的 API。"""

    def __init__(
        self,
        session_service_url: str,
        account_id: str,
        api_key: str = "",
        mode: str = "cloudmobile",
    ):
        self._base = session_service_url.rstrip("/")
        self._account_id = account_id
        self._mode = mode
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self._base,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            headers=headers,
        )

    def capture(
        self, trace_id: str, include_cursor: bool = True
    ) -> tuple[str, int, int]:
        resp = self._client.post(
            "/api/v1/session/screenshot",
            json={
                "account_id": self._account_id,
                "trace_id": trace_id,
                "include_cursor": include_cursor,
            },
        )
        resp.raise_for_status()
        d = resp.json()
        if not d.get("ok", False):
            raise RuntimeError(f"[{self._mode}] 远程截图失败: {d.get('error', 'unknown')}")
        return d["base64"], d.get("cursor_x", 0), d.get("cursor_y", 0)

    def perform(self, trace_id: str, action: dict) -> dict:
        resp = self._client.post(
            "/api/v1/session/action",
            json={
                "account_id": self._account_id,
                "trace_id": trace_id,
                "action": action,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()


class RemoteDelegateStrategy:
    """委托型 strategy（远程版）：通过 HTTP 调 Session Service 的 /session/delegate-task。"""

    name = "remote_delegate"

    def __init__(
        self,
        session_service_url: str,
        account_id: str,
        api_key: str = "",
        timeout_seconds: int = 300,
    ):
        self._base = session_service_url.rstrip("/")
        self._account_id = account_id
        self._timeout = timeout_seconds
        self._headers = {}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        ctx.cancel.raise_if_cancelled()

        logger.info(
            {
                "msg": "[agentbay] 远程委托 mobile_use Agent",
                "subtask": subtask.id,
                "goal": subtask.goal,
                "timeout_s": self._timeout,
            },
            ctx.trace_id,
        )

        try:
            async with httpx.AsyncClient(
                base_url=self._base,
                timeout=httpx.Timeout(connect=5.0, read=float(self._timeout + 60), write=10.0, pool=5.0),
                headers=self._headers,
            ) as client:
                resp = await client.post(
                    "/api/v1/session/delegate-task",
                    json={
                        "account_id": self._account_id,
                        "trace_id": ctx.trace_id,
                        "goal": subtask.goal,
                        "max_steps": 50,
                        "timeout_seconds": self._timeout,
                    },
                )
                resp.raise_for_status()
                d = resp.json()
        except Exception as e:
            logger.error({"msg": "[agentbay] 远程委托失败", "error": str(e)}, ctx.trace_id)
            return TaskResult.failure(AgentError(f"[agentbay] 远程委托失败: {e}"))

        ctx.cancel.raise_if_cancelled()

        ok = d.get("ok", False)
        summary = d.get("summary", "")
        error_msg = d.get("error", "")

        logger.info(
            {
                "msg": "[agentbay] 远程委托返回",
                "ok": ok,
                "summary": (summary[:200] + "…") if len(summary) > 200 else summary,
                "error": error_msg,
            },
            ctx.trace_id,
        )

        if ok:
            return TaskResult.success(summary=str(summary))
        return TaskResult.failure(AgentError(error_msg or "[agentbay] 远程委托未成功完成"))


__all__ = ["RemoteEnv", "RemoteDelegateStrategy"]
