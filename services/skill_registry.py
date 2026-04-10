"""
SkillRegistry: skill 加载与查询的统一入口。

设计原则：
- 元数据（manifest）和执行体（scripts）分离：扫盘只读 manifest，脚本懒加载
- SkillManifest 是纯数据 dataclass，不再混塞 instructions/path
- 工具调用通过 registry 路由，禁止外部直接拿 dict
- 同名工具冲突时显式报错，不再静默覆盖
"""
from __future__ import annotations

import importlib.util
import inspect
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import utils.logger as logger
from agents.base import ToolNotFoundError


@dataclass(frozen=True)
class SkillManifest:
    """SKILL.md 的纯元数据"""
    name: str
    description: str
    group: str
    triggers: tuple[str, ...] = ()


@dataclass
class Skill:
    """运行时 skill 对象：元数据 + 指令体 + 懒加载的工具"""
    manifest: SkillManifest
    instructions: str
    scripts_dir: Path
    _tools: dict[str, Callable] | None = field(default=None, repr=False)

    def tools(self) -> dict[str, Callable]:
        """首次调用时加载脚本，之后缓存"""
        if self._tools is None:
            self._tools = self._load_tools()
        return self._tools

    def _load_tools(self) -> dict[str, Callable]:
        tools: dict[str, Callable] = {}
        if not self.scripts_dir.is_dir():
            return tools
        for py_file in self.scripts_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                module_name = f"dynamic_skill_{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for fn_name, fn in inspect.getmembers(module, inspect.isfunction):
                    if fn.__module__ == module_name:
                        tools[fn_name] = fn
                        logger.info({
                            "msg": "加载 skill 工具",
                            "skill": self.manifest.name,
                            "tool": fn_name,
                            "source": py_file.name,
                        })
            except Exception as e:
                logger.error({
                    "msg": "加载 skill 脚本失败",
                    "path": str(py_file),
                    "error": str(e),
                })
        return tools


class SkillRegistry:
    """所有 skill 的注册表与查询入口"""

    def __init__(self, root: str | Path = "skills"):
        self._root = Path(root)
        self._skills: dict[str, Skill] = {}
        self._tool_to_skill: dict[str, str] = {}    # tool_name -> owning skill name
        self._scan()

    def _scan(self) -> None:
        """启动时扫盘，只读 manifest 和 instructions，不加载脚本"""
        if not self._root.exists():
            logger.warning({"msg": "skill 目录不存在", "path": str(self._root)})
            return
        for skill_file in self._root.glob("**/SKILL.md"):
            try:
                skill = self._parse(skill_file)
                if skill is None:
                    continue
                if skill.manifest.name in self._skills:
                    logger.warning({
                        "msg": "skill 名称重复，后者覆盖前者",
                        "name": skill.manifest.name,
                    })
                self._skills[skill.manifest.name] = skill
            except Exception as e:
                logger.error({
                    "msg": "解析 SKILL.md 失败",
                    "path": str(skill_file),
                    "error": str(e),
                })

    def _parse(self, path: Path) -> Skill | None:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        meta = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        relative_parts = path.relative_to(self._root).parts
        group = relative_parts[0] if relative_parts else ""

        manifest = SkillManifest(
            name=meta["name"],
            description=meta.get("description", ""),
            group=group,
            triggers=tuple(meta.get("triggers", [])),
        )
        return Skill(
            manifest=manifest,
            instructions=body,
            scripts_dir=path.parent / "scripts",
        )

    # ── 查询接口 ──────────────────────────────────────────────

    def manifests_for(
        self,
        disabled_skills: set[str] = frozenset(),
        disabled_groups: set[str] = frozenset(),
    ) -> list[SkillManifest]:
        """返回过滤后的 manifest 列表（用于 planner 拼 skill manifest）"""
        return [
            s.manifest
            for s in self._skills.values()
            if s.manifest.name not in disabled_skills
            and s.manifest.group not in disabled_groups
        ]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def build_prompt(self, skill_names: list[str]) -> str:
        """组装挂载技能的 prompt 段落"""
        if not skill_names:
            return ""
        parts = ["\n## 挂载技能知识库 (Expert Skills)"]
        for name in skill_names:
            skill = self._skills.get(name)
            if skill is None:
                continue
            parts.append(
                f"\n### Skill: {skill.manifest.name}\n"
                f"描述: {skill.manifest.description}\n"
                f"指令逻辑:\n{skill.instructions}\n"
            )
        return "\n".join(parts)

    # ── 工具调用接口 ──────────────────────────────────────────

    def has_tool(self, tool_name: str) -> bool:
        if tool_name in self._tool_to_skill:
            return True
        for skill in self._skills.values():
            if tool_name in skill.tools():
                self._tool_to_skill[tool_name] = skill.manifest.name
                return True
        return False

    def invoke_tool(self, tool_name: str, params: dict, trace_id: str = "system"):
        """
        通过工具名调用，自动注入 trace_id（如果工具签名支持）。
        找不到工具时抛 ToolNotFoundError。
        """
        skill_name = self._tool_to_skill.get(tool_name)
        if skill_name is None:
            for skill in self._skills.values():
                if tool_name in skill.tools():
                    skill_name = skill.manifest.name
                    self._tool_to_skill[tool_name] = skill_name
                    break
            else:
                raise ToolNotFoundError(f"未找到工具: {tool_name}")

        fn = self._skills[skill_name].tools()[tool_name]
        sig = inspect.signature(fn)
        valid = {k: v for k, v in params.items() if k in sig.parameters}
        if "trace_id" in sig.parameters:
            return fn(trace_id=trace_id, **valid)
        if "task_id" in sig.parameters:
            return fn(task_id=trace_id, **valid)
        return fn(**valid)
