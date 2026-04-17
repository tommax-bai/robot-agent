# 新人架构说明

本文档用于帮助新人快速理解项目。你可以先按“10 分钟理解链路”读一遍，再按模块逐步深入。

## 1. 项目一句话

这是一个基于视觉语言模型的 GUI Agent：它根据目标生成计划，截图理解当前页面，把动作分发给可插拔的执行后端（本地 Chrome 或阿里无影云手机），再截图再操作，当前主要面向小红书内容探索、互动和发布场景。

核心闭环：

```text
目标
  -> Planner 拆子任务
  -> 截图 Observation（Backend.screenshot）
  -> LLM 生成 Decision
  -> ActionDispatcher 分发动作
  -> Backend.execute_action / Skill 执行
  -> 再截图验证
  -> finish 或继续
```

通过 `agent.runtime.mode` 在三种执行模式间切换：

| mode | Backend（执行环境） | Strategy（决策方式） |
|------|--------------------|---------------------|
| `local_chrome`（默认） | `MacOSChromeBackend`：PyAutoGUI + ImageGrab | `VisionActionStep`：自家 VLM |
| `cloudmobile` | `AgentBayBackend`：阿里无影云手机 | `VisionActionStep`：同上 |
| `agentbay` | `AgentBayBackend`（共享 session） | `AgentBayDelegateStrategy`：委托给 AgentBay 内置 mobile_use Agent |

现在项目还加入了“可重复动作快路径”：

```text
截图
  -> PageContext 页面状态
  -> RecipeStore 匹配动作链
  -> VisualLocator 用 OpenCV 找按钮/图标
  -> RecipeOperator 快速执行
  -> 失败则回退 VisionActionStep + LLM
```

## 2. 目录分层

```text
agents/       Agent 决策与编排层
api/          FastAPI HTTP / WebSocket 接口
config.py     全局配置，密钥从环境变量读取
dto/          API 请求 DTO
runtime/      运行时上下文和依赖注入容器
services/     可复用业务服务
skills/       可挂载的领域技能与动态工具
tools/        与外部环境交互的工具
utils/        日志、初始化、JSON、LLM client 等基础设施
prompts/      Planner / Operator / Strategist 使用的 prompt
docs/         开发规范和新人文档
```

分层原则：

1. `agents/` 决定“做什么”。
2. `services/` 管理“业务状态和可复用能力”。
3. `tools/` 执行“外部世界动作”。
4. `runtime/` 负责“把依赖串起来”。
5. `api/` 只做“请求入口和响应输出”。

## 3. 启动链路

入口是 `app.py`。

启动时发生：

1. 非 Docker 环境加载 `.{APP_ENV}.env`，默认 `.dev.env`。
2. `config.validate()` 校验配置结构。
3. `utils.init_functions.init.init()` 初始化屏幕、系统信息、Chrome 配置和 Chrome client。
4. `runtime.container.init_container()` 构造 `AppContainer`。
5. `container.supervisor.start_scheduler()` 启动自动调度。
6. FastAPI 注册路由并对外服务。

本地启动：

```bash
source .venv/bin/activate
export APP_ENV=dev
uvicorn app:app --host 0.0.0.0 --port 6702 --reload
```

生产/长跑方式：

```bash
export APP_ENV=dev
gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:6702 --timeout 120
```

## 4. 依赖注入容器

`runtime/container.py::AppContainer` 是应用级对象工厂。它只在启动时构造一次。

里面会创建：

1. `JsonFileStateRepo`：读写 `data/agent_state.json`。
2. `LlmTool`：LLM 调用门面。
3. `SkillRegistry`：扫描 `skills/`。
4. `IntentRegistry`：加载 `data/intent_registry.json`，Planner 和 BehaviorSummarizer 共享的 intent 词表。
5. `PageContextCache`：缓存页面分类结果。
6. `VisualLocator`：OpenCV / 坐标定位。
7. `RecipeStore`、`TraceRecorder`、`BehaviorSummarizer`：动作记忆链路。
8. `Backend`：按 `agent.runtime.mode` 由 `_build_backend()` 构造（`MacOSChromeBackend` 或 `AgentBayBackend`）。
9. `Strategy`：按同样的 mode 由 `_build_strategy()` 构造（`VisionActionStep` 或 `AgentBayDelegateStrategy`）。
10. `Strategist`、`Planner`、`ActionDispatcher`（注入 backend）、`VisionActionStep`（注入 backend）、`RecipeOperator`（注入 backend）、`SubtaskRunner`（注入 strategy）、`Operator`、`Supervisor`。

新人修改依赖连线时，优先看 `AppContainer`，不要在业务函数里临时创建全局对象。新增执行模式时，往 `_build_backend()` 和 `_build_strategy()` 里加分支即可，agent 层不动。

## 5. 任务执行主链路

HTTP 同步任务入口：

```text
POST /api/v1/agent/actions/sync
  -> api/v1/route/agent.py
  -> Supervisor.execute_task()
  -> Operator.run()
  -> Planner.generate_plan()  # 返回带 intent 标签的子任务列表
  -> SubtaskRunner.run()
  -> RecipeOperator.try_run()  # 用 subtask.intent + page_state 精确匹配 recipe
  -> VisionActionStep.run()   # 快路径失败或没有 recipe 时走 LLM
```

`Supervisor` 的职责：

1. 管理模式：`waiting`、`patrolling`、`executing`、`debug`。
2. 抢占当前任务。
3. 为任务创建 `RunContext`。
4. 启动/停止调度循环。

`Operator` 的职责：

1. 初始化 history。
2. 调 Planner 生成子任务。
3. 逐个执行子任务。
4. 聚合 `TaskResult`。

`SubtaskRunner` 的职责：

1. 先尝试 recipe 快路径（mode 无关，所有模式都受益于已沉淀的动作链）。
2. 未命中时调用注入的 `SubtaskStrategy.run()` —— 具体实现由 mode 决定。
3. 遇到 `StepBudgetExceededError` 时按 `max_resumes` 续航重试（仅 vision 循环会抛此异常；委托模式自带超时）。

## 5.1 Backend 与 Strategy 抽象

两个正交的扩展点把"环境"和"决策"解耦：

- **`ActionBackend`**（`tools/backends/__init__.py`）回答"在哪执行原子动作"。Protocol 只有两个方法：`screenshot(trace_id, include_cursor) -> (b64, mx, my)` 和 `execute_action(trace_id, {method, params, finish}) -> dict`。三个消费方（`ActionDispatcher`、`VisionActionStep`、`RecipeOperator`）通过构造函数注入 backend，永远不直接 import `tools.actions` / `tools.screenshot`。
- **`SubtaskStrategy`**（`agents/operator/strategy.py`）回答"如何把 SubTask 变成 TaskResult"。Protocol：`name + async run(subtask, ctx) -> TaskResult`。两种实现：
  - `VisionActionStep`：自家 VLM 视觉循环（mode 1/2）
  - `AgentBayDelegateStrategy`：把整个子任务交给 AgentBay 内置 mobile_use Agent 黑盒（agentbay）

云端 session 是懒加载的：构造 `AgentBayBackend` 不会建实例，第一次 `screenshot()` 或 `ensure_session()` 才真正产生云端计费。`GET /api/v1/agent/runtime` 返回 `session_active` 字段供查看。

新增动作或新增模式的影响面：
- 新增**只用于本地**的原子动作：改 `tools/actions.py` + `prompts/operator/action.md`。
- 新增**跨模式**的原子动作：同时改 `tools/actions.py` 和 `tools/backends/agentbay.py` 的 `match` 块。在云端无意义的动作（如 `move` 光标）应返回 `ok=True` 加 message 说明，避免打断 LLM 链路。
- 新增**第四种执行模式**：在 `tools/backends/` 加新 backend 实现 → 在 `runtime/container._build_backend` 加分支 → 视情况加新 strategy。

## 6. Vision-Action 循环

`agents/operator/vision_action.py::VisionActionStep` 是最核心的慢路径。

一轮循环包含：

1. `_observe()`：调 `backend.screenshot(...)`，得到 `Observation(image_base64, captured_at)`。
2. `_think()`：把截图和上一步结果发给 LLM，得到 `Decision`。
3. `Decision.parse()`：清洗 LLM 输出，转成 `Action` 列表。
4. `_dispatcher.dispatch()`：执行 action。
5. `TraceRecorder.record_llm_step()`：记录可复用轨迹。

LLM 的坐标采用 0-1000 归一化坐标，每个 backend 内部自行换算成像素：
- `MacOSChromeBackend` 走 `tools/screen.py::llm_to_screen()`，使用当前 Chrome 窗口位置。
- `AgentBayBackend` 走 `_llm_to_pixel()`，使用最近一次 `beta_take_screenshot()` 缓存的屏幕宽高。

## 7. Action 分发

`ActionDispatcher` 做三种分发：

1. `finish`：任务结束，返回 summary。
2. skill tool：如果 `SkillRegistry` 里存在同名工具，调用技能脚本。
3. atomic action：否则调用注入的 `backend.execute_action()`，由对应 backend 转发到 PyAutoGUI（本地）或 AgentBay session（云端）。

常见原子动作：

```text
click / dblclick / move / scroll / drag / paste / copy / wait / hotkey
```

`MacOSChromeBackend` 由 PyAutoGUI 执行，带人类化移动、抖动、等待。`AgentBayBackend` 通过 `session.mobile.tap/swipe/input_text/send_key` 走云端 Android 原生事件注入。

## 8. Skill 系统

`skills/` 下每个技能目录包含：

```text
SKILL.md
scripts/*.py
```

`SKILL.md` 负责给 LLM 注入领域知识，例如小红书登录、探索、发布规范。`scripts/*.py` 提供可以被 `ActionDispatcher` 直接调用的工具函数。

加载方式：

1. 启动时 `SkillRegistry` 扫描 `SKILL.md`，只读 manifest 和 instructions。
2. 真正调用 tool 时才懒加载脚本。
3. 工具函数如果接受 `trace_id` 参数，registry 会自动注入。

新增技能时：

1. 写 `SKILL.md`，声明 `name`、`description`、`triggers`。
2. 需要稳定自动化能力时，在 `scripts/` 里写函数。
3. 函数返回 `{"ok": bool, "message": str, "finish": bool}`。

## 9. 动作记忆与 OpenCV 快路径

为了解决 LLM 单轮截图分析约 30 秒的问题，项目新增了动作记忆链路。

模块：

```text
services/action_memory/recipe.py          Recipe 数据结构
services/action_memory/recipe_store.py    从 data/action_recipes 读取 recipe
services/action_memory/trace_recorder.py  记录 LLM/recipe 执行轨迹
services/action_memory/behavior_summarizer.py  LLM 旁路总结可复用行为
services/vision/page_context.py           页面上下文缓存
services/vision/page_classifier.py        页面分类协议与基础结构
services/vision/llm_page_classifier.py    LLM 页面分类器
services/vision/page_matcher.py           本地 landmark 页面匹配器
services/vision/page_registry.py          已知页面语义库
services/vision/locator.py                point/template 定位
prompts/vision/page_classifier.md         页面分类 LLM prompt
prompts/action_memory/behavior_summarizer.md  行为总结 LLM prompt
agents/operator/recipe_operator.py        recipe 快路径执行者
```

当前定位能力：

1. `point`：复用归一化点位，适合临时或低风险动作。
2. `template`：用 OpenCV 在截图中匹配图标/按钮模板，适合稳定 UI 元素。

快路径安全策略：

1. 没有 recipe：直接走 LLM。
2. recipe 未启用或置信度不足：不执行。
3. locator 找不到：标记 unsafe，回退 LLM。
4. action 执行失败：记录 failed，回退后续链路。
5. 新挖掘候选 recipe 默认 disabled，需要人工或评估器提升。

旁路学习链路：

```text
VisionActionStep 成功完成 subtask
  -> TraceRecorder 已记录 llm_step
  -> SubtaskRunner 调度 BehaviorSummarizer
  -> BehaviorSummarizer 后台读取 trace 并调用 LLM
  -> 写入 data/action_recipe_candidates/<trace_id>/<subtask_id>_llm.json
     (trial=true, enabled=false)
```

试运行提升链路：

```text
下次同 intent + page_state 命中候选 recipe
  -> RecipeOperator 以"试运行"模式执行
  -> 成功 → trial_successes += 1
     累计 ≥ 2 次 → 自动提升为正式 recipe，移入 data/action_recipes/
  -> 失败 → trial_failures += 1
     累计 ≥ 3 次 → 永久禁用（trial=false, enabled=false）
  -> 试运行期间失败自动回退 LLM，不影响主流程
```

候选 recipe 不阻塞主线，不会直接启用。只有通过实际回放验证后才会自动提升。

## 10. Intent 注册表

`services/intent_registry.py::IntentRegistry` 是 Planner 和 BehaviorSummarizer 的共享语义标签词表。

数据源：`data/intent_registry.json`（已纳入 git 版本控制）。

作用：

1. **Planner 端**：prompt 中注入候选 intent 列表，LLM 从中选择，输出经 `resolve()` 归一化后写入 `SubTask.intent`。
2. **Summarizer 端**：同样注入候选列表，生成的 recipe 也带规范化 intent。
3. **RecipeStore 端**：匹配时优先对比 `subtask.intent == recipe.intent`（精确匹配），不再依赖关键词碰运气。

生命周期：

```text
首次出现：Planner 分配 intent → LLM 执行 → Summarizer 产出 recipe（继承同一 intent）
后续匹配：Planner 再次选择同一 intent → RecipeStore 精确命中 → 跳过 LLM
增长：Summarizer 输出新 intent → 人工审核后加入 intent_registry.json
```

新增 intent 时只需编辑 JSON 文件，无需改 Python 代码。

## 11. 页面分类

`PageClassifier` 是页面识别的扩展点。当前容器默认使用 `LlmPageClassifier`：

```text
Observation
  -> PageContextCache 同 trace 精确截图缓存
  -> 缓存未命中：主线先返回 pending/unknown，不阻塞操作循环
  -> 后台分类任务：LocalPageMatcher 尝试用稳定 landmark 本地命中
  -> 本地未命中：后台调用 LLM 判断页面
  -> LLM 参考 PageRegistry 中的已知页面摘要，优先复用已有 page_state
  -> 置信度达标：写入 data/page_registry/pages.json，并保存 stable/dynamic/negative landmark
```

这里不使用全图 average hash 自动匹配页面。内容社区页面的搜索结果、首页推荐流、个人主页内容会占据大部分截图面积，内容一变，全图 hash 就会失真；布局相似但语义不同的页面也可能被误判。本地快速路径只验证 LLM 识别出的稳定小元素，例如固定图标或按钮模板；动态内容区会被记录但不会参与页面命中。后台分类每个 trace 只保留最新截图任务，避免过期截图排队阻塞。

页面分类结果结构：

```python
PageClassification(
    page_state="rednote_search_results",
    layout_type="feed_grid",
    confidence=0.86,
    is_new_page=True,
    description="小红书搜索结果列表页",
    evidence=["顶部有搜索框", "页面中有瀑布流笔记卡片"],
    stable_landmarks=[...],
    dynamic_regions=[...],
    negative_landmarks=[...],
)
```

常见页面状态建议命名：

```text
rednote_home
rednote_search_results
rednote_filter_panel
rednote_note_detail
rednote_login
rednote_publish_home
rednote_publish_editor
unknown
```

页面分类不属于 operator 的职责。推荐链路是：

```text
Observation -> PageClassifier -> PageContext -> RecipeStore.match()
```

页面库文件默认位置：

```text
data/page_registry/pages.json
```

它记录每类页面的说明、证据、出现次数和样本截图 hash。这个目录在 `data/` 下，默认不提交；如果要把某些页面定义固化为团队资产，可以后续再设计一份人工审核后的 `config/page_states/*.json`。

## 12. 状态与知识积累

`services/agent_state.py` 管理长期状态，默认写入 `data/agent_state.json`。

核心字段：

```text
inspiration_pool
title_few_shots
hashtags
anxiety_keywords
knowledge_topics
learning_notes
recent_searches
followers_history
daily_stats
```

`services/knowledge.py::harvest_knowledge()` 会从任务 summary 中识别：

```text
[SHOT]
[CONTENT]
[TAG]
[LEARNING]
[ANXIETY]
[KNOWLEDGE]
[MOOD]
[INSIGHT]
```

然后写回 state。新增状态字段时，必须同步更新：

1. `DEFAULT_STATE`
2. `_apply_patch()`
3. `AgentStateRepo.update()` Protocol 签名
4. 文档或使用方

## 13. 调度系统

调度相关文件：

```text
agents/supervisor/supervisor.py
agents/supervisor/scheduler.py
agents/supervisor/scheduled_jobs.py
```

职责划分：

1. `supervisor.py`：模式、抢占、上下文、当前任务。
2. `scheduler.py`：按时间段判断当前该跑什么。
3. `scheduled_jobs.py`：每类任务怎么生成目标、怎么收尾。

新增定时任务时，优先在 `scheduled_jobs.py` 添加 handler，再挂到 `SCHEDULED_JOB_HANDLERS`，不要把业务逻辑塞进 supervisor。

## 14. API 入口

常用接口：

```text
GET  /health
GET  /api/v1/agent/status
GET  /api/v1/agent/runtime           # 当前 mode / backend / strategy / AgentBay session 状态
POST /api/v1/agent/actions
POST /api/v1/agent/actions/sync
POST /api/v1/agent/patrol
POST /api/v1/agent/mode/debug
POST /api/v1/agent/mode/waiting
GET  /api/v1/usage/daily_stats
GET  /api/v1/usage/details
GET  /api/v1/usage/list_dates
```

调试建议：

```bash
curl http://127.0.0.1:6702/health
curl http://127.0.0.1:6702/api/v1/agent/status
curl -X POST http://127.0.0.1:6702/api/v1/agent/mode/debug
```

同步执行一个最小任务：

```bash
curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "观察当前页面，并说明页面中央区域是什么，不做多余操作"}'
```

## 15. PHP 背景同学的 Python 心智模型

### 模块不是类文件

Python 一个 `.py` 文件就是模块，里面可以放函数、类、常量。导入时模块顶层代码会执行一次。因此不要在模块顶层做重 IO、发请求或启动线程，除非这是明确的启动文件。

### 缩进就是语法

Python 不用 `{}` 表示代码块，缩进就是结构。格式不只是风格，还是语义。

### `self` 必须显式写

```python
class Operator:
    def __init__(self, planner):
        self._planner = planner
```

PHP 里 `$this` 是隐式对象变量；Python 方法第一个参数显式接收实例，习惯命名为 `self`。

### dict 和 object 不一样

```python
data["goal"]  # dict
task.goal     # dataclass/object
```

跨模块稳定结构优先用 dataclass，不要把所有东西都做成 dict。

### `None` 是空值，不是空字符串

```python
if value is None:
    ...
```

判断 None 用 `is None`，不要用 `== None`。

### 异常类型要具体

PHP 里可能常见 `catch (\Throwable $e)` 兜底；Python 可以兜底 `except Exception as e`，但解析 JSON、文件不存在、网络超时等场景优先捕获具体异常。

### import 会执行模块顶层代码

循环 import 是 Python 项目常见坑。只为类型提示需要的导入，使用：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.context import RunContext
```

### async 需要一路 await

调用 async 函数必须 `await`，否则只是得到 coroutine 对象，不会真正执行。

```python
result = await runner.run(subtask, ctx)
```

## 16. 新人第一天建议阅读顺序

1. `docs/architecture_onboarding.md`：先理解链路。
2. `docs/development_guide.md`：看代码规范和 Python 习惯。
3. `runtime/container.py`：看对象如何串起来。
4. `agents/base.py`：看核心数据结构。
5. `agents/operator/vision_action.py`：看截图-LLM-动作循环。
6. `agents/operator/recipe_operator.py`：看快路径。
7. `services/action_memory/` 和 `services/vision/`：看动作记忆与定位。
8. `skills/rednote/*/SKILL.md`：看领域规则如何注入。

## 17. 常见改动入口

新增一个 LLM 行为规则：

```text
改 prompts/operator/*.md 或 skills/**/SKILL.md
```

新增一个稳定工具动作：

```text
改 skills/<domain>/<skill>/scripts/*.py
```

新增一个原子 PyAutoGUI 动作（仅本地）：

```text
改 tools/actions.py 和 prompts/operator/action.md
```

新增一个跨模式的原子动作（本地 + 云手机都要支持）：

```text
改 tools/actions.py（macOS 实现）
改 tools/backends/agentbay.py 的 execute_action 的 match 块（云手机映射）
改 prompts/operator/action.md（让 LLM 知道有这个动作）
```

切换执行模式：

```text
设置环境变量 AGENT_RUNTIME_MODE=local_chrome | cloudmobile | agentbay
云端模式必填 AGENTBAY_API_KEY，可选 AGENTBAY_IMAGE_ID / AGENTBAY_SCREENSHOT_FORMAT
启动后 curl /api/v1/agent/runtime 验证当前模式与 backend / strategy
```

新增第四种执行模式（例如桌面云电脑、其他厂商的云手机）：

```text
在 tools/backends/ 新建实现，满足 ActionBackend Protocol
在 runtime/container.py::_build_backend 加 mode 分支
若需要不同决策方式，新增一个 SubtaskStrategy 实现并在 _build_strategy 加分支
```

新增一个定时任务：

```text
改 agents/supervisor/scheduled_jobs.py
改 config.agent["schedule"]["daily_schedule"]
```

新增一个可复用动作链：

```text
先让 LLM 跑出成功 trace
BehaviorSummarizer 自动在后台产出候选到 data/action_recipe_candidates/
人工审查 JSON：确认 intent 与 data/intent_registry.json 一致
补 locator（point → template）、page_state、success 条件
移动到 data/action_recipes 并设 enabled=true
```

新增一个 intent 标签：

```text
编辑 data/intent_registry.json，增加 intent 条目
Planner 和 BehaviorSummarizer 下次调用时自动可见
无需改 Python 代码
```

## 18. 当前重点演进方向

1. 页面分类从 `unknown` 扩展到可识别小红书关键页面。
2. 为常用按钮积累 OpenCV template。
3. 给 recipe 增加更明确的 success check。
4. 为 recipe 候选增加自动降权/升权评估策略。
5. 建立 pytest smoke tests，覆盖 parser、recipe matching、locator、dispatcher。
