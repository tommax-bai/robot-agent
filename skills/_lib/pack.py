"""Pack 声明：一个 pack = 元数据 + 一组带 @tool 装饰的函数 + 一份 instructions.md。

用法（在 `skills/<pack_dir>/__init__.py` 里）:

    from skills import Pack, ToolContext, ToolOutcome

    pack = Pack(
        name="rednote.auth",
        description="...",
        supports=("local_chrome",),
    )

    @pack.tool
    def open_rednote_homepage(ctx: ToolContext) -> ToolOutcome:
        ...

instructions.md 在 loader 扫描时从同目录自动读取，pack 不用操心。
"""

from __future__ import annotations

from typing import Callable

from skills._lib.types import ToolOutcome, ToolSpec

ToolFn = Callable[..., ToolOutcome]


class Pack:
    """在 pack 模块作用域创建。`@pack.tool` 装饰器把函数登记为可调用的工具。"""

    def __init__(
        self,
        name: str,
        description: str,
        *,
        supports: tuple[str, ...] = ("local_chrome", "cloudmobile", "agentbay"),
    ):
        if not name:
            raise ValueError("Pack.name 不能为空")
        self.name = name
        self.description = description
        self.supports = tuple(supports)
        self._tools: dict[str, ToolSpec] = {}
        # instructions.md 的正文由 loader 在扫描时写入
        self.instructions: str = ""

    def tool(
        self,
        fn: ToolFn | None = None,
        *,
        name: str | None = None,
        supports: tuple[str, ...] | None = None,
    ) -> ToolFn:
        """注册一个工具函数。

        裸用:  @pack.tool                       → 函数名即工具名，继承 pack.supports
        改名:  @pack.tool(name=)                → 显式命名（少见）
        限模式: @pack.tool(supports=("local_chrome",))  →
            pack 整体扩到 cloudmobile，但本工具实现是 selenium-only，只在 local_chrome 暴露
        """
        def _register(f: ToolFn) -> ToolFn:
            short = name or f.__name__
            if short in self._tools:
                raise RuntimeError(f"pack={self.name} 已注册同名工具: {short}")
            doc = (f.__doc__ or "").strip()
            self._tools[short] = ToolSpec(
                pack=self.name, name=short, description=doc, fn=f,
                supports=tuple(supports) if supports is not None else None,
            )
            return f

        if fn is None:
            return _register  # type: ignore[return-value]
        return _register(fn)

    def tools(self) -> list[ToolSpec]:
        return list(self._tools.values())
