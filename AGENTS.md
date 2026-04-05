# REA Agent - 技能系统说明

## 1. 目录结构
- `agent/`: 核心逻辑 (Planner, React, Loader)
- `skills/`: 本地技能书 (SKILL.md)
- `prompts/`: 基础指令与规则

## 2. 如何添加一个新技能 (Skill)
1. 在 `skills/` 下创建一个目录，例如 `skills/douyin/`。
2. 在该目录下创建 `SKILL.md`。
3. `SKILL.md` 必须包含 YAML 元数据头：
   ```yaml
   ---
   name: douyin-post
   description: 抖音发布技能
   triggers:
     - 抖音
     - 发视频
   ---
   ```
4. 在下方编写具体的 Markdown 指令。

## 3. 核心组件
- **Planner (规划器)**: 将用户目标拆解为子任务，并匹配对应的 Skill。
- **SkillLoader (加载器)**: 负责发现和读取本地的 `SKILL.md` 文件。
- **ReAct Loop (执行器)**: 针对具体的子任务目标，结合 Skill 指令进行视觉分析与操作。

## 4. 优势
- **模块化**: 增加新功能只需写 MD 文件。
- **可换模型**: 由于是本地读取注入，支持 Gemini, Claude, GPT 等多种 VLM。
- **精准度**: 子任务模式让模型上下文更干净，坐标判断更准。
