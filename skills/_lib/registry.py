"""SkillRegistry: 对 agents / runtime 侧暴露的统一入口。

职责：
- 启动时通过 loader 发现所有 pack，建索引（pack.name → PackSpec，tool.fqname → ToolSpec）。
- 提供查询：列出 packs、按名取 pack、为 prompt 组装 skill 指令段。
- 提供调用：`invoke(name, params, trace_id)` —— 支持短名（历史兼容）与 fqname。
- 未找到工具抛 `ToolNotFoundError`；参数里传入未知 key 直接抛错（不再静默丢弃）。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Iterable

import utils.logger as logger
from agents.base import ToolNotFoundError
from skills._lib.loader import discover_packs
from skills._lib.pack import Pack
from skills._lib.types import PackSpec, ToolContext, ToolOutcome, ToolSpec


class SkillRegistry:
    def __init__(self, root: str | Path = "skills"):
        self._packs: dict[str, Pack] = {}
        self._tools_fq: dict[str, ToolSpec] = {}   # "pack.tool" -> spec
        self._tools_short: dict[str, ToolSpec] = {}  # "tool" -> spec（短名若唯一）
        self._short_ambiguous: set[str] = set()
        self._pack_of_tool: dict[str, str] = {}    # fqname -> pack.name（invoke 时用来查 supports）

        for pack in discover_packs(root):
            self._register(pack)

    def _register(self, pack: Pack) -> None:
        self._packs[pack.name] = pack
        for spec in pack.tools():
            fq = spec.fqname
            if fq in self._tools_fq:
                raise RuntimeError(f"工具 fqname 重复: {fq}")
            self._tools_fq[fq] = spec
            self._pack_of_tool[fq] = pack.name
            if spec.name in self._short_ambiguous:
                continue
            if spec.name in self._tools_short:
                # 短名冲突：两者都无法用短名调用，退化为 fqname
                self._short_ambiguous.add(spec.name)
                del self._tools_short[spec.name]
                logger.warning({
                    "msg": "工具短名冲突，仅 fqname 可调用",
                    "short": spec.name,
                })
            else:
                self._tools_short[spec.name] = spec

    # ─── 查询 ─────────────────────────────────────────────────

    def packs(self) -> list[PackSpec]:
        return [self._spec_of(p) for p in self._packs.values()]

    def pack(self, name: str) -> PackSpec | None:
        p = self._packs.get(name)
        return self._spec_of(p) if p else None

    def _spec_of(self, p: Pack) -> PackSpec:
        return PackSpec(
            name=p.name,
            description=p.description,
            supports=p.supports,
            instructions=p.instructions,
            tools=tuple(p.tools()),
        )

    def prompt_section(self, pack_names: Iterable[str], mode: str | None = None) -> str:
        """拼装挂载技能的 prompt 段落。

        - `mode`：当前 runtime mode（chromelocal / wuyingcloud / agentbay）。给出后：
          · pack 整体 `supports` 不含该 mode → 整个 pack 段落隐藏；
          · pack 支持该 mode，但其中某个工具自声明了更严格的 `supports` 不含该 mode →
            仅该工具从"可用工具"列表中剔除（instructions 仍然暴露；适合 pack 是
            "指令 + 少量 mode-specific 工具"的混合场景）。
        - 每个 pack 的工具块自带签名（参数名）和描述，让 LLM 清楚 params 只能含声明的 key。
        """
        parts: list[str] = []
        for name in pack_names:
            p = self._packs.get(name)
            if p is None:
                continue
            if mode is not None and p.supports and mode not in p.supports:
                logger.debug({
                    "msg": "跳过不支持当前 runtime 的 skill",
                    "pack": p.name, "supports": list(p.supports), "mode": mode,
                })
                continue
            tools_block = _format_tools(p, mode=mode)
            parts.append(
                f"\n### Skill: {p.name}\n"
                f"描述: {p.description}\n"
                f"可用工具：\n{tools_block}\n"
                f"指令逻辑:\n{p.instructions}\n"
            )
        if not parts:
            return ""
        header = (
            "\n## 挂载技能知识库 (Expert Skills)\n"
            "> **严格规则**（违反会被直接拒绝，不会重试）：\n"
            "> 1. 只能调用下方 **\"可用工具\"** 段列出的工具名；instructions 里提到的其它工具名（例如不同 runtime 下的专用工具）在**当前 runtime 不可用**，调用会立刻失败。\n"
            "> 2. `params` 必须只包含工具签名声明的参数名；不要自行追加 `description` / `reason` / `note` 等未声明字段。\n"
            "> 3. 若 instructions 引用的工具不在可用列表中，改用通用原子动作（click / drag / scroll / paste）完成同样的目标。\n"
        )
        return header + "".join(parts)

    # ─── 调用 ─────────────────────────────────────────────────

    def has_tool(self, name: str) -> bool:
        return name in self._tools_fq or name in self._tools_short

    def invoke(self, name: str, params: dict, trace_id: str = "system", mode: str | None = None) -> dict:
        spec = self._resolve(name)
        # mode 守卫：tool-level supports 优先于 pack-level supports
        if mode is not None:
            pack_name = self._pack_of_tool.get(spec.fqname)
            pack = self._packs.get(pack_name) if pack_name else None
            effective = _effective_supports(spec, pack)
            if effective and mode not in effective:
                raise ToolNotFoundError(
                    f"工具 {spec.fqname} 不支持当前 runtime={mode}"
                    f"（仅支持: {list(effective)}）"
                )
        ctx = ToolContext(trace_id=trace_id)
        bound = _bind_kwargs(spec, ctx, params)
        result = spec.fn(**bound)
        if not isinstance(result, ToolOutcome):
            raise RuntimeError(
                f"工具 {spec.fqname} 必须返回 ToolOutcome，实际: {type(result).__name__}"
            )
        return result.to_dict()

    def _resolve(self, name: str) -> ToolSpec:
        spec = self._tools_fq.get(name) or self._tools_short.get(name)
        if spec is None:
            if name in self._short_ambiguous:
                raise ToolNotFoundError(
                    f"工具名 {name!r} 在多个 pack 中存在，请用 fqname（pack.tool）调用"
                )
            raise ToolNotFoundError(f"未找到工具: {name}")
        return spec

    # ─── 向后兼容的旧方法名 ───────────────────────────────────
    # 旧 API: get / get_all / build_prompt / invoke_tool
    # 保留最小包装避免一次性改动所有 caller。

    def get_all(self) -> list[PackSpec]:
        return self.packs()

    def get(self, name: str) -> PackSpec | None:
        return self.pack(name)

    def build_prompt(self, skill_names: list[str], mode: str | None = None) -> str:
        return self.prompt_section(skill_names, mode=mode)

    def invoke_tool(self, tool_name: str, params: dict, trace_id: str = "system", mode: str | None = None) -> dict:
        return self.invoke(tool_name, params, trace_id, mode=mode)


def _format_tools(pack: Pack, mode: str | None = None) -> str:
    """把 pack 下所有工具格式化成 prompt 列表，含参数名（剔除 ctx）和描述。

    mode 给出时，剔除 tool-level `supports` 不含该 mode 的工具（pack 层已通过的前提下）。
    """
    lines: list[str] = []
    for spec in pack.tools():
        effective = _effective_supports(spec, pack)
        if mode is not None and effective and mode not in effective:
            continue
        sig = inspect.signature(spec.fn)
        params = [n for n in sig.parameters.keys() if n != "ctx"]
        sig_str = "(无参数)" if not params else "(" + ", ".join(params) + ")"
        desc = (spec.description or "").strip().split("\n")[0] or "（无描述）"
        lines.append(f"- `{spec.name}` {sig_str} — {desc}")
    return "\n".join(lines) if lines else "（无可在当前 runtime 使用的工具）"


def _effective_supports(spec: ToolSpec, pack: Pack | None) -> tuple[str, ...]:
    """工具的实际生效 supports：tool-level 优先，None 则继承 pack-level。"""
    if spec.supports is not None:
        return spec.supports
    if pack is not None:
        return pack.supports
    return ()


def _bind_kwargs(spec: ToolSpec, ctx: ToolContext, params: dict) -> dict:
    """把 (ctx, params) 映射成函数实际需要的 kwargs。

    - 如果函数参数里有名为 `ctx` 的，注入 ToolContext。
    - 其余 kwargs 必须全部出现在函数签名里，否则抛 TypeError —— 不再静默丢弃。
    """
    sig = inspect.signature(spec.fn)
    kwargs: dict = {}

    param_names = set(sig.parameters.keys())
    if "ctx" in param_names:
        kwargs["ctx"] = ctx

    unknown = [k for k in params if k not in param_names]
    if unknown:
        raise TypeError(
            f"工具 {spec.fqname} 不接受参数: {unknown}（可用: {sorted(param_names - {'ctx'})}）"
        )
    kwargs.update(params)
    return kwargs
