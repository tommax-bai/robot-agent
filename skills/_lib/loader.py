"""Pack 发现与加载。

只做两件事：
1. 扫 `skills/` 根目录下的子目录（跳过 `_` 前缀），将每个子目录作为一个 pack 导入。
2. 读取同目录的 `instructions.md`（如有），写入 pack.instructions。

失败立刻抛错，不静默跳过 —— 本层是启动期的"声明式契约"，错配应尽早暴露。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import utils.logger as logger
from skills._lib.pack import Pack


def discover_packs(root: str | Path = "skills") -> list[Pack]:
    root_path = Path(root)
    if not root_path.is_dir():
        logger.warning({"msg": "skills 根目录不存在", "path": str(root_path)})
        return []

    packs: list[Pack] = []
    seen_names: set[str] = set()

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue

        module_name = f"skills.{entry.name}"
        module = importlib.import_module(module_name)

        pack = getattr(module, "pack", None)
        if not isinstance(pack, Pack):
            raise RuntimeError(
                f"{module_name}/__init__.py 必须暴露名为 'pack' 的 Pack 实例"
            )
        if pack.name in seen_names:
            raise RuntimeError(f"pack 名称重复: {pack.name}")
        seen_names.add(pack.name)

        instr_file = entry / "instructions.md"
        if instr_file.exists():
            pack.instructions = instr_file.read_text(encoding="utf-8").strip()

        packs.append(pack)

    return packs
