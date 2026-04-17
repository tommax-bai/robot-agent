"""
AgentBayDelegateStrategy: 把整个子任务委托给 AgentBay 内置的 mobile_use Agent 执行。

适用于 mode=agentbay。SubtaskRunner 跳过自家 VLM 视觉循环，直接把
subtask.goal 喂给 session.agent.mobile.execute_task()，由 AgentBay 端的
LLM/Agent 负责 think + act + 终止判定，本端只负责订阅流式事件并把结果
包装成 TaskResult。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import AgentError, TaskResult
from tools.backends.session_manager import is_session_dead_error

if TYPE_CHECKING:
    from agents.base import SubTask
    from runtime.context import RunContext
    from tools.backends.session_manager import SessionManager

_DEFAULT_TASK_TIMEOUT_SECONDS = 300


class AgentBayDelegateStrategy:
    """委托型 strategy：把 subtask 目标发给 AgentBay 的 mobile_use Agent，本地只等结果。"""

    name = "agentbay_delegate"

    def __init__(
        self,
        session_mgr: SessionManager,
        timeout_seconds: int = _DEFAULT_TASK_TIMEOUT_SECONDS,
    ):
        self._session_mgr = session_mgr
        self._timeout = timeout_seconds

    async def run(self, subtask: SubTask, ctx: RunContext) -> TaskResult:
        ctx.cancel.raise_if_cancelled()
        session = self._session_mgr.acquire(ctx.trace_id)
        mobile_agent = session.agent.mobile

        logger.info(
            {
                "msg": "[agentbay] 委托云端 mobile_use Agent 执行子任务",
                "subtask": subtask.id,
                "goal": subtask.goal,
                "timeout_s": self._timeout,
            },
            ctx.trace_id,
        )

        content_buf: list[str] = []
        tool_calls: list[dict] = []

        def _on_content(event):
            text = getattr(event, "content", "") or ""
            if text:
                content_buf.append(text)

        def _on_tool_call(event):
            tool_calls.append(
                {
                    "tool": getattr(event, "tool_name", ""),
                    "args": getattr(event, "args", {}) or {},
                }
            )

        try:
            exec_handle = await asyncio.to_thread(
                mobile_agent.execute_task,
                subtask.goal,
                50,
                None,
                _on_content,
                _on_tool_call,
            )
        except Exception as e:
            logger.error({"msg": "[agentbay] mobile_use 启动任务异常", "error": str(e)}, ctx.trace_id)
            return TaskResult.failure(AgentError(f"[agentbay] mobile_use 启动失败: {e}"))

        task_id = getattr(exec_handle, "task_id", "")
        ws_handle = getattr(exec_handle, "_ws_handle", None)
        logger.info(
            {"msg": "[agentbay] mobile_use 任务已起", "task_id": task_id or "(streaming, no id)"},
            ctx.trace_id,
        )

        async def _cancel_watcher():
            while True:
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    return
                if ctx.cancel.cancelled:
                    if ws_handle is not None:
                        logger.info({"msg": "[agentbay] 检测到 cancel，调 ws_handle.cancel() 中止流式任务"}, ctx.trace_id)
                        try:
                            await asyncio.to_thread(ws_handle.cancel)
                        except Exception as e:
                            logger.warning({"msg": "[agentbay] ws_handle.cancel 异常", "error": str(e)}, ctx.trace_id)
                    elif task_id:
                        logger.info({"msg": "[agentbay] 检测到 cancel，调 terminate_task", "task_id": task_id}, ctx.trace_id)
                        try:
                            await asyncio.to_thread(mobile_agent.terminate_task, task_id)
                        except Exception as e:
                            logger.warning({"msg": "[agentbay] terminate_task 异常", "error": str(e)}, ctx.trace_id)
                    else:
                        logger.warning(
                            {"msg": "[agentbay] 无 ws_handle 也无 task_id，cancel 信号无法送达云端（任务会跑到 max_steps 自然结束）"},
                            ctx.trace_id,
                        )
                    return

        watcher = asyncio.create_task(_cancel_watcher())
        try:
            result = await asyncio.to_thread(exec_handle.wait, self._timeout)
        except Exception as e:
            logger.error({"msg": "[agentbay] mobile_use wait 异常", "error": str(e)}, ctx.trace_id)
            if is_session_dead_error(e):
                self._session_mgr.mark_dead("strategy.wait", str(e), ctx.trace_id)
            return TaskResult.failure(AgentError(f"[agentbay] mobile_use 等待失败: {e}"))
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

        ctx.cancel.raise_if_cancelled()

        ok = bool(getattr(result, "success", False))
        error_msg = getattr(result, "error_message", "") or ""

        notes = [
            str(c["args"].get("text", "")).strip()
            for c in tool_calls
            if c["tool"] == "take_note" and c["args"].get("text")
        ]
        agent_text = "".join(content_buf).strip()

        sections: list[str] = []
        if notes:
            sections.append("【观察结果】\n" + "\n\n".join(notes))
        if agent_text:
            sections.append("【执行过程】\n" + agent_text)
        summary = "\n\n".join(sections) or (getattr(result, "task_result", None) or "")

        logger.info(
            {
                "msg": "[agentbay] mobile_use Agent 返回",
                "ok": ok,
                "task_id": getattr(result, "task_id", ""),
                "task_status": getattr(result, "task_status", ""),
                "tool_calls": len(tool_calls),
                "notes": len(notes),
                "summary": (summary[:200] + "…") if len(summary) > 200 else summary,
                "error_message": error_msg,
            },
            ctx.trace_id,
        )
        if tool_calls:
            logger.info(
                {
                    "msg": "[agentbay] mobile_use 动作链",
                    "calls": [f"{c['tool']}({c['args']})" for c in tool_calls[:10]],
                    "total": len(tool_calls),
                },
                ctx.trace_id,
            )

        if ok:
            return TaskResult.success(summary=str(summary))
        return TaskResult.failure(AgentError(error_msg or "[agentbay] mobile_use 未成功完成"))
