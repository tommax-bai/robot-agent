import os
import yaml
import json
import re
import importlib.util
import inspect
from pathlib import Path
from typing import Dict, List, Optional, Callable

def normalize_dict_keys(obj):
    """递归归一化字典键名，移除多余的引号和空格"""
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if isinstance(k, str):
                # 移除键名两端可能存在的引号和空格
                new_key = k.strip().strip('"').strip("'").strip()
                new_obj[new_key] = normalize_dict_keys(v)
            else:
                new_obj[k] = normalize_dict_keys(v)
        return new_obj
    elif isinstance(obj, list):
        return [normalize_dict_keys(i) for i in obj]
    else:
        return obj

def extract_json(raw_text: str) -> dict:
    """从原始文本中提取 JSON 对象，支持 Markdown 代码块包裹，并具备更强的容错能力"""
    if not raw_text:
        raise ValueError("模型输出为空")
    
    text = raw_text.strip()
    
    # 1. 尝试直接解析（最快）
    try:
        return normalize_dict_keys(json.loads(text))
    except json.JSONDecodeError:
        pass

    # 2. 尝试正则提取第一个 {...} 或 [...]
    # 使用 DOTALL 模式匹配跨行 JSON，非贪婪匹配获取第一个完整的结构
    # 优先匹配 { } 结构，因为我们的决策通常是对象
    match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if match:
        json_str = match.group(1)
        
        # 尝试清理常见的 Markdown 干扰
        if json_str.startswith("```json"):
            json_str = json_str[7:]
        if json_str.endswith("```"):
            json_str = json_str[:-3]
        json_str = json_str.strip()

        try:
            return normalize_dict_keys(json.loads(json_str))
        except json.JSONDecodeError:
            # 3. 最后的挣扎：处理被截断或格式极其混乱的情况
            try:
                # 尝试修复一些常见的转义错误
                fixed_str = re.sub(r'\\\"', '"', json_str)
                # 移除键名周围可能存在的单引号包装
                fixed_str = re.sub(r'\'\"(\w+)\"\':', r'"\1":', fixed_str)
                return normalize_dict_keys(json.loads(fixed_str))
            except:
                pass
    
    # 如果正则也没救回来，抛出更详细的错误
    raise ValueError(f"无法从输出中提取有效的 JSON。原始输出: {raw_text[:200]}...")

class SkillLoader:
    def __init__(self, skills_root: str = "skills"):
        self.skills_root = Path(skills_root)
        self.skills: Dict[str, dict] = {}
        self.custom_tools: Dict[str, Callable] = {} # 存放动态加载的函数: method_name -> func
        self.load_all_skills()

    def load_all_skills(self):
        """扫描 skills 目录，加载所有符合条件的 SKILL.md 及其对应的 scripts"""
        if not self.skills_root.exists():
            return

        for skill_file in self.skills_root.glob("**/SKILL.md"):
            try:
                skill_data = self._parse_skill_file(skill_file)
                if skill_data:
                    self.skills[skill_data['name']] = skill_data
                    # 动态加载该技能下的 scripts
                    scripts_dir = skill_file.parent / "scripts"
                    if scripts_dir.exists() and scripts_dir.is_dir():
                        self._load_scripts_from_dir(scripts_dir)
            except Exception as e:
                print(f"加载技能文件 {skill_file} 失败: {e}")

    def _load_scripts_from_dir(self, scripts_dir: Path):
        """动态加载目录下的所有 .py 文件中的函数"""
        for py_file in scripts_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            
            try:
                module_name = f"dynamic_skill_{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # 提取模块中定义的所有函数
                    for name, func in inspect.getmembers(module, inspect.isfunction):
                        # 只加载在该模块中定义的函数，忽略 import 进来的
                        if func.__module__ == module_name:
                            self.custom_tools[name] = func
                            print(f"成功挂载动态工具: {name} (来自 {py_file.name})")
            except Exception as e:
                print(f"加载脚本 {py_file} 失败: {e}")

    def _parse_skill_file(self, path: Path) -> Optional[dict]:
        """解析单个 SKILL.md 文件 (YAML frontmatter + Markdown)"""
        try:
            content = path.read_text(encoding='utf-8')
            if not content.startswith('---'):
                return None

            # 拆分 YAML 头和 Markdown 正文
            parts = content.split('---', 2)
            if len(parts) < 3:
                return None
                
            frontmatter = parts[1]
            body = parts[2]
            
            meta = yaml.safe_load(frontmatter)
            relative_parts = path.relative_to(self.skills_root).parts
            meta['group'] = relative_parts[0] if relative_parts else ""
            meta['instructions'] = body.strip()
            meta['full_path'] = str(path)
            meta['dir_path'] = str(path.parent)
            return meta
        except Exception as e:
            print(f"解析 {path} 失败: {e}")
            return None

    def get_skill_by_name(self, name: str) -> Optional[dict]:
        return self.skills.get(name)

    def match_skills(self, user_goal: str) -> List[str]:
        """根据用户目标简单匹配合适的技能"""
        matched = []
        user_goal_lower = user_goal.lower()
        for name, meta in self.skills.items():
            if name.lower() in user_goal_lower:
                matched.append(name)
                continue
            
            triggers = meta.get('triggers', [])
            if any(t.lower() in user_goal_lower for t in triggers):
                matched.append(name)
                continue
                
            desc = meta.get('description', '').lower()
            if any(word in desc for word in user_goal_lower.split()):
                matched.append(name)
        
        return list(set(matched))

    def build_skill_prompt(self, skill_names: List[str]) -> str:
        """组装选定技能的 Prompt 内容"""
        if not skill_names:
            return ""
        
        prompt_segments = ["\n## 挂载技能知识库 (Expert Skills)"]
        for name in skill_names:
            skill = self.get_skill_by_name(name)
            if skill:
                segment = f"\n### Skill: {skill['name']}\n"
                segment += f"描述: {skill.get('description', '')}\n"
                segment += f"指令逻辑:\n{skill['instructions']}\n"
                prompt_segments.append(segment)
        
        return "\n".join(prompt_segments)
