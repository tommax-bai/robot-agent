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
- ControlSignal（CancelledError / StepBudgetExceededError 等） → 必须 raise 透传，是任务生命周期信号
- 其他 Exception → 转 ActionResult.failure（动作执行可能临时失败，让 LLM 看到错误自适应）
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agents.base import (
    ControlSignal,
    ToolNotFoundError,
)
from agents.operator.vision.types import Action, ActionOutcome, ActionResult
from utils.events import Ev, ev

if TYPE_CHECKING:
    from runtime.ctx import RunContext
    from services.skill_registry import SkillRegistry
    from tools.environment import Environment


class ActionDispatcher:
    def __init__(self, skills: SkillRegistry, env: Environment):
        self._skills = skills
        self._env = env

    def set_env(self, env: Environment) -> None:
        """替换底层 Environment（用于 runtime mode 热切换）。"""
        self._env = env

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
        ev(Ev.ACTION_TOOL_INVOKED, method=action.method, params=action.params)
        try:
            # skill 脚本多半含同步 IO（selenium/网络/文件），丢线程池别堵 loop
            payload = await asyncio.to_thread(
                self._skills.invoke_tool, action.method, action.params, trace_id=ctx.trace_id
            )
            return ActionResult(method=action.method, ok=True, payload=payload)
        except ControlSignal:
            raise
        except ToolNotFoundError as e:
            ev(Ev.ACTION_TOOL_FAILED, method=action.method, reason="not_found",
               error=str(e), level=30)  # WARNING: 幻觉而非崩溃
            return ActionResult(method=action.method, ok=False, message=str(e))
        except Exception as e:
            ev(Ev.ACTION_TOOL_FAILED, method=action.method, reason="exception",
               error=str(e), exc_info=True)
            return ActionResult(method=action.method, ok=False, message=str(e))

    async def _dispatch_atomic(self, action: Action, ctx: RunContext) -> ActionResult:
        try:
            # env.perform 对 cloud 模式是 HTTP 同步调用，必须丢线程
            raw = await asyncio.to_thread(
                self._env.perform,
                ctx.trace_id,
                {"method": action.method, "params": action.params},
            )
            ok = bool(raw.get("ok"))
            if ok:
                ev(Ev.ACTION_ATOMIC_OK, method=action.method)
            else:
                ev(Ev.ACTION_ATOMIC_FAILED, method=action.method,
                   reason="backend_not_ok", backend_message=str(raw.get("message", "")))
            return ActionResult(
                method=action.method,
                ok=ok,
                message=str(raw.get("message", "")),
                payload=raw,
            )
        except ControlSignal:
            raise
        except Exception as e:
            ev(Ev.ACTION_ATOMIC_FAILED, method=action.method, reason="exception",
               error=str(e), exc_info=True)
            return ActionResult(method=action.method, ok=False, message=str(e))
