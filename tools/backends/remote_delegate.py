"""
RemoteDelegateStrategy: agentbay 模式下通过 HTTP 调用 Session Service 的委托执行接口。

Brain Service 版的 AgentBayDelegateStrategy — 不直接持有 session，
而是发一个 HTTP 请求让 Session Service 跑 mobile_use Agent。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

import utils.logger as logger
from agents.base import AgentError, TaskResult

if TYPE_CHECKING:
    from agents.base import SubTask
    from runtime.context import RunContext


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
