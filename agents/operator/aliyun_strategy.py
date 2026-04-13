"""
AliyunMobileAgentStrategy: 把整个子任务委托给阿里 mobile_use Agent 黑盒执行。

适用于 mode=cloud_aliyun。SubtaskRunner 跳过自家 VLM 视觉循环，直接把
subtask.goal 喂给 session.agent.mobile_use.execute_task_and_wait()，
由阿里端负责 think + act + 终止判定，本端只负责把结果包装成 TaskResult。

Trade-off：
- 优势：实现极薄，省自己 VLM token，单步任务原型最快
- 劣势：决策黑盒不可观察、按阿里计费、出问题难调试
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import AgentError, TaskResult

if TYPE_CHECKING:
    from agents.base import SubTask
    from runtime.context import RunContext
    from tools.backends.agentbay import AgentBayBackend


# 单个子任务的最大等待时长（秒）。阿里端有自己的步数控制，这里只兜底超时。
_DEFAULT_TASK_TIMEOUT_SECONDS = 300


class AliyunMobileAgentStrategy:
    name = "aliyun_mobile_agent"

    def __init__(
        self,
        backend: AgentBayBackend,
        timeout_seconds: int = _DEFAULT_TASK_TIMEOUT_SECONDS,
    ):
        self._backend = backend
        self._timeout = timeout_seconds

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        ctx.cancel.raise_if_cancelled()
        session = self._backend.ensure_session(ctx.trace_id)
        mobile_agent = session.agent.mobile_use

        logger.info(
            {
                "msg": "委托阿里 mobile_use Agent 执行子任务",
                "subtask": subtask.id,
                "goal": subtask.goal,
                "timeout_s": self._timeout,
            },
            ctx.trace_id,
        )

        # SDK 是同步阻塞调用，丢到线程池里以免堵住 asyncio loop
        try:
            result = await asyncio.to_thread(
                mobile_agent.execute_task_and_wait,
                subtask.goal,
                self._timeout,
            )
        except Exception as e:
            logger.error(
                {"msg": "阿里 mobile_use Agent 调用异常", "error": str(e)},
                ctx.trace_id,
            )
            return TaskResult.failure(AgentError(f"aliyun mobile_use 调用失败: {e}"))

        ctx.cancel.raise_if_cancelled()

        # 不同 SDK 版本字段命名可能不一致，统一用 getattr 兜底
        ok = bool(getattr(result, "success", False))
        summary = (
            getattr(result, "result", None)
            or getattr(result, "content", None)
            or getattr(result, "summary", None)
            or ""
        )
        error_msg = getattr(result, "error_message", "") or ""

        logger.info(
            {
                "msg": "阿里 mobile_use Agent 返回",
                "ok": ok,
                "summary": (str(summary)[:120] + "…") if len(str(summary)) > 120 else str(summary),
                "error_message": error_msg,
            },
            ctx.trace_id,
        )

        if ok:
            return TaskResult.success(summary=str(summary))
        return TaskResult.failure(AgentError(error_msg or "aliyun mobile_use 未成功完成"))
