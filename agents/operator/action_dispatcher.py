"""
ActionDispatcher: 动作分发器。

负责把 Decision 中的 actions 列表路由到正确的执行器：
- A. skill registry 中的动态工具
- B. 'finish' 终止动作
- C. 原子动作（click / scroll / paste / ...）

设计原则：
- 不知道 vision-action 循环的存在，只接收一组 actions 和 ctx
- 每个动作之间检查取消，连招中场可中断
- 返回结构化 ActionOutcome，调用方无需 dict 索引

错误处理策略：
- ToolNotFoundError → 转 ActionResult.failure 反馈给 LLM（LLM 可能幻觉了工具名，给它机会重试）
- CancelledError → 必须 raise 透传，是任务取消信号
- 其他 Exception → 转 ActionResult.failure（动作执行可能临时失败，让 LLM 看到错误自适应）
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import utils.logger as logger
from agents.base import (
    Action,
    ActionOutcome,
    ActionResult,
    CancelledError,
    ToolNotFoundError,
)

if TYPE_CHECKING:
    from runtime.context import RunContext
    from services.skill_registry import SkillRegistry
    from tools.backends import ActionBackend


class ActionDispatcher:
    def __init__(self, skills: SkillRegistry, backend: ActionBackend):
        self._skills = skills
        self._backend = backend

    def set_backend(self, backend: ActionBackend) -> None:
        """替换底层 ActionBackend（用于 runtime mode 热切换）。"""
        self._backend = backend

    async def dispatch(self, actions: list[Action], ctx: RunContext) -> ActionOutcome:
        results: list[ActionResult] = []

        for idx, action in enumerate(actions):
            ctx.cancel.raise_if_cancelled()

            if action.method == "finish":
                summary = action.params.get("summary", "")
                results.append(ActionResult(method="finish", ok=True, message="task finished", payload=summary))
                return ActionOutcome.finished(summary, results)

            if self._skills.has_tool(action.method):
                result = await self._dispatch_skill_tool(action, ctx)
            else:
                result = await self._dispatch_atomic(action, ctx)

            results.append(result)

            # 连招间隔
            if idx < len(actions) - 1:
                await asyncio.sleep(action.delay or 0.5)

        return ActionOutcome.continuing(results)

    async def _dispatch_skill_tool(self, action: Action, ctx: RunContext) -> ActionResult:
        logger.info(
            {"msg": "执行动态工具", "method": action.method, "params": action.params},
            ctx.trace_id,
        )
        try:
            # skill 脚本多半含同步 IO（selenium/网络/文件），丢线程池别堵 loop
            payload = await asyncio.to_thread(
                self._skills.invoke_tool, action.method, action.params, trace_id=ctx.trace_id
            )
            return ActionResult(method=action.method, ok=True, payload=payload)
        except CancelledError:
            raise
        except ToolNotFoundError as e:
            logger.warning(
                {"msg": "工具不存在（LLM 可能幻觉）", "method": action.method},
                ctx.trace_id,
            )
            return ActionResult(method=action.method, ok=False, message=str(e))
        except Exception as e:
            logger.error(
                {"msg": "动态工具执行失败", "method": action.method, "error": str(e)},
                ctx.trace_id,
            )
            return ActionResult(method=action.method, ok=False, message=str(e))

    async def _dispatch_atomic(self, action: Action, ctx: RunContext) -> ActionResult:
        try:
            # backend.execute_action 对 cloud 模式是 HTTP 同步调用，必须丢线程
            raw = await asyncio.to_thread(
                self._backend.execute_action,
                ctx.trace_id,
                {"method": action.method, "params": action.params},
            )
            return ActionResult(
                method=action.method,
                ok=bool(raw.get("ok")),
                message=str(raw.get("message", "")),
                payload=raw,
            )
        except CancelledError:
            raise
        except Exception as e:
            logger.error(
                {"msg": "原子动作执行失败", "method": action.method, "error": str(e)},
                ctx.trace_id,
            )
            return ActionResult(method=action.method, ok=False, message=str(e))
