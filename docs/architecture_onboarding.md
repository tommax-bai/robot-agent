# 新人架构说明

本文档用于帮助新人快速理解项目。先按"10 分钟理解链路"读一遍，再按模块逐步深入。

## 1. 项目一句话

这是一个基于视觉语言模型的 GUI Agent：根据目标生成计划，截图理解当前页面，把动作分发给可插拔的执行环境（本地 Chrome 或阿里无影云手机），再截图再操作。当前面向小红书内容探索、互动、发布场景。

核心闭环：

```text
目标
  -> Planner 拆子任务（带 intent）
  -> 截图 Observation（Environment.capture）
  -> LLM 生成 Decision
  -> ActionDispatcher 分发动作
  -> Environment.perform / Skill 工具执行
  -> 再截图验证
  -> finish 或继续
```

## 2. 两个正交维度

### 2.1 运行时模式（`agent.runtime.mode`，每个 worker 独立）

| mode | Environment（在哪执行） | Strategy（怎么决策） |
|------|------------------------|----------------------|
| `local_chrome`（默认） | `MacOSChromeEnv`：PyAutoGUI + ImageGrab | `VisionActionStep`：自家 VLM |
| `cloudmobile` | `CloudMobileEnv`：阿里无影云手机 | `VisionActionStep`：自家 VLM |
| `agentbay` | `CloudMobileEnv`（共享 session） | `AgentBayDelegateStrategy`：委托给 AgentBay 内置 mobile_use Agent（黑盒） |

当配置了 `SESSION_SERVICE_URL` 时，`CloudMobileEnv` 会被 `RemoteEnv` 替换，`AgentBayDelegateStrategy` 会被 `RemoteDelegateStrategy` 替换——Agent 层完全无感。

### 2.2 进程拓扑（`AGENT_TOPOLOGY`，每个进程决定）

| topology | 角色 | 说明 |
|----------|------|------|
| `monolith`（默认） | brain + session 在同一进程 | 本地开发和单机生产 |
| `brain` | 只装决策层（Supervisor/Operator/Planner…） | 通过 HTTP 调远程 Session Service |
| `session` | 只管云手机 session | 通过 HTTP 暴露 `/session/*` 给 Brain |

**唯一入口是 `app.py`**。旧的 `app_brain.py` / `app_session.py` 已删除。拓扑由 env 决定，不需要切文件。

### 2.3 快路径复用

三种运行时模式都先走 `RecipeOperator` 快路径（mode 无关）：

```text
截图
  -> PageContext 页面状态
  -> RecipeStore 按 intent + page_state 精确匹配
  -> VisualLocator OpenCV template/point 定位
  -> 快速执行 → 成功则跳过 LLM
  -> 失败则回退 VisionActionStep
```

## 3. 目录分层

```text
agents/       Agent 决策与编排层
  operator/     Operator + SubtaskRunner + Resolver/Observer/RetryPolicy + Vision 子循环
  planner/      Planner + prompt/parser/normalizer/config
  strategist/   Strategist + brainstormer/generators/persona/…
  supervisor/   Supervisor + ModeState/RunSlot/Scheduler + jobs/
  base.py       共享数据类型与错误体系
  result_aggregation.py
api/          FastAPI 路由，按 topology 挂载
  v1/           tasks / workers / callbacks / usage / chrome_proxy（brain）
                sessions（session）
                events（所有拓扑，SSE）
bootstrap/    进程冷启动副作用（平台探测、Chrome 安装/启动）
config/       pydantic 配置（sections + topology + env 读取）
runtime/      Host / Worker / RunContext / EventBus / signals / wire
services/     state / intents / recipes / vision / knowledge / history / token_usage / …
skills/       Python 包形式的领域技能（rednote_auth / rednote_explorer / rednote_publish）
tools/        environment（协议 + macos_chrome/cloud_mobile/remote 实现）+ llm（门面）
utils/        日志、JSON、fs、http、prompt、events、banner 等基础设施
prompts/      Planner / Operator / Strategist / 页面分类 / 行为总结的 prompt
data/         运行期数据（agent_state / action_traces / action_recipes / page_registry / intent_registry.json）
docs/         开发规范和新人文档
```

分层原则：

1. `agents/` 决定"做什么"。
2. `services/` 管理"业务状态和可复用能力"。
3. `tools/` 执行"外部世界动作"（通过 `Environment` 协议）。
4. `skills/` 沉淀"领域知识 + 稳定自动化工具"。
5. `runtime/` 负责"把依赖串起来 + 生命周期"。
6. `bootstrap/` 只做"冷启动时的系统探测和外部进程准备"。
7. `api/` 只做"请求入口和响应输出"，按拓扑挂载。

## 4. 启动链路

入口是 `app.py`。启动时发生：

1. 非 Docker 环境加载 `.{APP_ENV}.env`，默认 `.dev.env`。
2. `config.validate()` 做跨 section 校验（pydantic 构造时已校验结构与类型）。
3. `logger.configure()` 重装日志。
4. 若 `topology.needs_local_chrome`：`bootstrap.boot()` 做平台探测 + Chrome 安装/启动。
5. `Host.boot(topology)` 构造进程级 DI 容器。
6. `attach_log_bridge(host.events)`：把日志桥接到 EventBus，供 dashboard SSE 用。
7. 遍历 `host.list()` 启动每个 worker 的 `supervisor.start()`。
8. `topology.has_brain` 时安装 SIGINT 升级器。
9. `register_routers(app, topology)` 按拓扑挂路由。

开发启动：

```bash
source .venv/bin/activate
export APP_ENV=dev
uvicorn app:app --host 0.0.0.0 --port 6702 --reload
```

生产（单机）：

```bash
AGENT_HTTP_PORT=6702 gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app -b 0.0.0.0:6702 --timeout 120
```

生产（拓扑拆分）：

```bash
# Session 节点
AGENT_TOPOLOGY=session AGENTBAY_API_KEY=... uvicorn app:app --port 6710

# Brain 节点
AGENT_TOPOLOGY=brain SESSION_SERVICE_URL=http://session-host:6710 \
  SESSION_SERVICE_API_KEY=... uvicorn app:app --port 6702
```

## 5. 运行时容器：Host + Worker

取代了旧的 `AppContainer` / `SessionAppContainer` / `WorkerPool` / `SessionWorker`。

### 5.1 Host（`runtime/host.py`）

进程级单例。持有：

- `events: EventBus`
- `workers: dict[account_id, Worker]`
- brain/monolith 拓扑下：`llm`, `skills`, `intents`, `page_registry`, `page_matcher`, `page_classifier`, `page_context_cache`, `visual_locator`, `recipe_store`, `trace_recorder`, `behavior_summarizer`, `planner`（所有 worker 共享）

`Host.boot(topology)` 是唯一入口；`Host.current()` 在请求处理里取全局单例。

`Host.add_worker(cfg)` / `remove_worker(id)` 支持运行时动态增删，不写回配置（重启即消失）。

### 5.2 Worker（`runtime/worker.py`）

单账号执行单元。两种形态：

- **完整 Worker**（monolith/brain）：除了共享服务外，自带 `SessionManager` + `Environment` + `ActionDispatcher` + `VisionActionStep` + `RecipeOperator` + `SubtaskStrategy` + `SubtaskRunner` + `Operator` + `Strategist` + `Supervisor`。
- **精简 Worker**（session-only，`minimal=True`）：只有 `SessionManager` + `Environment` + display 元数据。

热切换：`worker.swap_runtime_mode(new_mode)` 在 supervisor 不忙时切换 env + strategy；session 同时释放。

### 5.3 wire 工厂（`runtime/wire.py`）

纯函数：

- `build_session(mode, account_cfg, events, account_id)` — 云端模式建 `SessionManager`，本地/remote 返回 None。
- `build_env(mode, account_cfg, session_mgr)` — 按 mode + `session_service_url` 返回 `MacOSChromeEnv` / `CloudMobileEnv` / `RemoteEnv`。
- `build_strategy(mode, vision_step, session_mgr, account_cfg)` — 按 mode + remote 决定 `VisionActionStep` / `AgentBayDelegateStrategy` / `RemoteDelegateStrategy`。
- `state_file_for(account_cfg)` — 决定 state 文件路径。

只有 `Host` 和 `Worker` 应该 import 这个模块。

## 6. 任务执行主链路

```text
POST /api/v1/agent/actions/sync
  -> api/v1/tasks.py::submit_sync
  -> resolve_worker(account_id).supervisor.submit_task(task, trace_id)
  -> Supervisor: RunSlot.preempt() → ModeState → EXECUTING → _run_with_slot
  -> Operator.run(task, ctx)
    -> Planner.generate_plan(ctx, task.goal)      # 返回带 intent 的子任务
    -> for each subtask:
        SubtaskRunner.run(subtask, ctx)
          for resolver in [RecipeResolver, StrategyResolver]:
            RecipeResolver → RecipeOperator.try_run()
              match subtask.intent + page_state → recipe 命中
              执行 steps → resolved（跳过 LLM）
              未命中 → skipped
            StrategyResolver → strategy.run() + RetryPolicy
              VisionActionStep：_observe → _think → _act 循环
              AgentBayDelegateStrategy：委托 mobile_use（本地 session）
              RemoteDelegateStrategy：HTTP 委托给 Session Service
          BehaviorSummarizerObserver.on_complete()   # 旁路挖掘候选 recipe
    -> agents.result_aggregation.aggregate(...)
  -> TaskResult → HTTP response
```

### 6.1 Supervisor 架构（拆分后）

| 组件 | 文件 | 职责 |
|------|------|------|
| `Supervisor` | `agents/supervisor/supervisor.py` | 编排 + 三个任务入口 + 四个生命周期入口 |
| `ModeState` | `mode_state.py` | WAITING/PATROLLING/EXECUTING/DEBUG 状态机 |
| `RunSlot` | `run_slot.py` | 单槽任务 claim / preempt / drain |
| `RunContextFactory` | `run_context_factory.py` | 生成 RunContext |
| `Scheduler` | `scheduler.py` | 时间片调度循环 |
| `JobRegistry` + `Job` | `jobs/*.py` | `PatrolJob`, `PostJob`, `SimpleGoalJob("dm"/"cr")` |

新增定时任务只需在 `jobs/` 下实现 `ScheduledJob` 并 `registry.register()`，不用动 Supervisor。

### 6.2 SubtaskRunner 架构（拆分后）

旧的 if/else 硬编码路径被替换成：

- **Resolvers**（有序列表，首个 resolved / failed 胜出，skipped 继续）：
  - `RecipeResolver` — 调 `RecipeOperator.try_run()`
  - `StrategyResolver` — 调注入的 `SubtaskStrategy.run()`，持有 `RetryPolicy`
- **Observers**（completed 后触发）：`BehaviorSummarizerObserver` 等。生命周期信号（取消/预算耗尽）穿透 observer 回主循环。
- **RetryPolicy** — `ResumeOnBudgetPolicy(max_resumes=2)`（默认）在 `StepBudgetExceededError` 时续航。
- **StopPolicy** — `StopOnFirstFailure`（默认）聚合子任务时决定是否短路。

hot-swap strategy 走 `runner.set_strategy(new)`，内部转发到 `StrategyResolver`。

### 6.3 Vision 循环（拆分后）

`agents/operator/vision/` 子包：

- `step.py` — `VisionActionStep`（核心 observe/think/act 循环）
- `config.py` — `VisionConfig`（步数预算、温度、模型等）
- `prompt_builder.py` — `VisionPromptBuilder`（预加载 action.md + skill 注入）
- `history.py` — 对话历史缓冲
- `decision_parser.py` — LLM 输出清洗（4 种形状、key 归一化、坐标兜底）
- `step_feedback.py` — 上一轮结果如何回传给下一轮
- `types.py` — 内部数据类型

## 7. Environment 抽象（取代旧 Backend）

`tools/environment.py` 定义 `Environment` Protocol：

```python
class Environment(Protocol):
    def capture(self, trace_id: str, include_cursor: bool = True) -> tuple[str, int, int]: ...
    def perform(self, trace_id: str, action: dict) -> dict: ...
```

三个内置实现：

| Environment | 文件 | 适用 |
|-------------|------|------|
| `MacOSChromeEnv` | `tools/macos_chrome/__init__.py` | local_chrome |
| `CloudMobileEnv` | `tools/cloud_mobile/__init__.py` | cloudmobile / agentbay（本地 session） |
| `RemoteEnv` | `tools/remote/__init__.py` | brain 拓扑，通过 HTTP 调 Session Service |

消费方（`ActionDispatcher` / `VisionActionStep` / `RecipeOperator`）只接 Protocol，不 import 具体实现。

### 7.1 协程式参数兜底

`tools/environment.py::coerce_param(params, key, default)` 处理 LLM 输出里常见的脏 key（带引号、空格、大小写不一致）。所有 env 内部用它，避免各自写一份。

### 7.2 新增原子动作的修改面

- **只用于本地**：改 `tools/macos_chrome/actions.py` + `prompts/operator/action.md`。
- **跨模式**：上面两处 + `tools/cloud_mobile/__init__.py` 的 `_ACTION_HANDLERS` 映射。云手机无意义的动作（如 `move` 光标）放进 `_NOOP_ACTIONS` 返回 `ok=True` 不打断 LLM。
- **新增第四种执行环境**（桌面云电脑等）：在 `tools/` 下建包 → 实现 `Environment` Protocol → 在 `runtime/wire.py::build_env` 加分支。

## 8. Action 分发

`ActionDispatcher.dispatch(decision, env, ctx)` 三种分发：

1. `finish` → `TaskResult.success(summary)`
2. 匹配 skill tool → `SkillRegistry.invoke_tool(name, params, ToolContext)`
3. 匹配原子动作 → `env.perform(trace_id, {method, params, finish})`

常见原子动作：`click / dblclick / move / scroll / drag / paste / copy / wait / hotkey`。

## 9. Skill 系统（新版）

**重要变化**：Skills 不再是 YAML manifest + scripts，而是纯 Python 包：

```text
skills/_lib/               Pack / ToolContext / ToolOutcome / SkillRegistry / loader
skills/rednote_auth/
  __init__.py              pack = Pack(...); @pack.tool def open_rednote_homepage(ctx): ...
  instructions.md          给 LLM 注入的领域说明
skills/rednote_explorer/
skills/rednote_publish/
```

写一个 skill：

```python
from skills import Pack, ToolContext, ToolOutcome

pack = Pack(
    name="rednote.auth",
    description="小红书账户登录与状态检查。",
    supports=("local_chrome",),
)

@pack.tool
def open_rednote_homepage(ctx: ToolContext) -> ToolOutcome:
    ...
    return ToolOutcome.success("已打开小红书")
```

加载：

1. 启动时 `SkillRegistry` 扫描 `skills/*/` 包发现 `pack`，只读 metadata + instructions。
2. 真正调用 tool 时才执行函数体。
3. `supports` tuple 限定这个 pack 在哪些 runtime.mode 下生效。

## 10. Recipe 动作记忆

为了解决 LLM 单轮 30 秒的瓶颈，沉淀"可验证动作链"。模块位置变了：

```text
services/recipes/
  model.py      ActionRecipe / RecipeStep / RecipeAttempt
  repo.py       RecipeStore（正式库 + 候选库、匹配、trial 生命周期）
  recorder.py   TraceRecorder（每一步 LLM/recipe 决策写 JSONL）
  miner.py      BehaviorSummarizer（从成功轨迹 LLM 挖候选）

services/vision/
  types.py      共享数据类型
  locator.py    VisualLocator（point + OpenCV template）
  pages.py      PageRegistry
  classifier.py UnknownPageClassifier / LocalPageMatcher / LlmPageClassifier
  cache.py      PageContextCache（trace 内 hash 缓存 + 可选后台分类）
```

快路径决策：

```text
RecipeResolver
  -> RecipeStore.match(subtask.intent, page_state)
    -> 命中正式 recipe：正常执行
    -> 命中候选（trial=true）：试运行模式
         成功累计 ≥ 2 → 自动提升为正式
         失败累计 ≥ 3 → 永久禁用
    -> 未命中 → skipped（主流程走 LLM）
  -> 执行失败 → 降级 LLM，不循环硬跑
```

旁路挖掘：

```text
subtask 成功
  -> BehaviorSummarizerObserver 通知 BehaviorSummarizer
  -> 后台读 data/action_traces/<trace_id>.jsonl
  -> LLM 总结 → 写 data/action_recipe_candidates/<trace_id>/<subtask_id>_llm.json
  -> 默认 enabled=false, trial=true
```

## 11. Intent 注册表

`services/intents.py::IntentRegistry` 是 Planner 和 RecipeMiner 的共享语义标签词表。

数据源：`data/intent_registry.json`（已纳入 git）。

作用：

1. **Planner 端**：prompt 中注入候选 intent，LLM 选择，`resolve()` 归一化后写入 `SubTask.intent`。
2. **Miner 端**：同样注入，生成的 recipe 也带规范化 intent。
3. **RecipeStore 端**：`subtask.intent == recipe.intent` 精确匹配。

新增 intent 只编辑 JSON，无需改 Python。

## 12. 页面分类

默认 `LlmPageClassifier`：

```text
Observation
  -> PageContextCache 按 trace + 截图 hash 精确缓存
  -> 缓存未命中：主线先拿 pending/unknown 继续（不阻塞）
  -> 后台任务：LocalPageMatcher 尝试用 stable landmark template 命中
  -> 本地未命中：调用 LLM 判断页面
  -> 参考 PageRegistry 里的已知页面摘要，优先复用 page_state
  -> 置信度达标：写 data/page_registry/pages.json
```

不使用全图 hash，动态内容面积大会失真。只验证 stable landmark 的小模板区域；dynamic region 只记录不参与命中。

## 13. 状态与知识积累

`services/state.py::JsonFileStateRepo` 默认写 `data/accounts/{id}/agent_state.json`（有多账号时）或 `data/agent_state.json`（默认 worker）。

核心字段（见 `DEFAULT_STATE`）：

```text
inspiration_pool / title_few_shots / hashtags / anxiety_keywords /
knowledge_topics / learning_notes / recent_searches / followers_history /
mood / last_discovery / last_check_time / last_post_date / last_post_trace_id /
daily_stats
```

`services/knowledge.py::harvest_knowledge()` 从任务 summary 识别：

```text
[SHOT] [CONTENT] [TAG] [LEARNING] [ANXIETY] [KNOWLEDGE] [MOOD] [INSIGHT]
```

并写回 state。新增状态字段必须同步更新：

1. `DEFAULT_STATE`
2. `_apply_patch()`
3. `AgentStateRepo.update()` Protocol 签名

## 14. 多账号矩阵

`config.settings.agent.accounts` 列表：

```python
accounts = [
    {"id": "acct-a", "display_name": "导航员-A"},
    {"id": "acct-b", "display_name": "导航员-B", "mode": "cloudmobile"},
]
```

留空 → 单 `default` worker。每个 worker：

- 独立 `data/accounts/{id}/agent_state.json`
- 独立 AgentBay session
- 独立 `Supervisor`、runtime mode
- 共享 LLM / Skill / Intent / Page / Recipe / EventBus / Planner

运行时 CRUD（临时，重启消失）：`POST /api/v1/agent/workers`、`DELETE /api/v1/agent/workers/{id}`。要永久账号写配置。

每个云手机 session 首次使用都要扫码登录。不做镜像快照。session 销毁（mode 切换、idle 超时、release）后登录态丢失。

## 15. API 入口

按拓扑挂载（见 `api/v1/__init__.py::register_routers`）。

Brain 侧常用：

```text
GET  /health
GET  /api/v1/agent/status       [?account_id=...]
GET  /api/v1/agent/runtime
GET  /api/v1/agent/workers
POST /api/v1/agent/workers      {count, id?, display_name?, image_id?}
DEL  /api/v1/agent/workers/{id}
POST /api/v1/agent/runtime/mode {mode, account_id?}
POST /api/v1/agent/actions      {user_goal, account_id?}
POST /api/v1/agent/actions/sync
POST /api/v1/agent/cancel       {account_id?}
POST /api/v1/agent/mode/debug | /agent/mode/waiting | /agent/patrol
POST /api/v1/agent/scheduled/patrol-once
POST /api/v1/agent/session/release {account_id?, force?}
GET  /api/v1/agent/live-url     {account_id?}
GET  /api/v1/agent/screen.jpg   (local_chrome)
GET  /api/v1/usage/daily_stats | /usage/details | /usage/list_dates
GET  /api/v1/events             (SSE)
```

Session 侧（内部 API，Brain 调）：

```text
POST /api/v1/session/acquire | /session/release
GET  /api/v1/session/status | /session/url
POST /api/v1/session/screenshot | /session/action
POST /api/v1/session/delegate-task
GET  /api/v1/session/workers | POST /session/workers | DEL /session/workers/{id}
GET  /api/v1/session/config | /session/health
```

## 16. PHP 背景同学的 Python 心智模型

### 模块不是类文件

Python 一个 `.py` 文件就是模块，里面可以放函数、类、常量。导入时模块顶层代码会执行一次。不要在模块顶层做重 IO 或启动线程。

### 缩进就是语法

Python 不用 `{}`，缩进就是结构，还是语义。

### `self` 必须显式写

```python
class Operator:
    def __init__(self, planner):
        self._planner = planner
```

### dict vs dataclass

跨模块稳定结构用 dataclass，不要把所有东西都做成 dict。

### `None` 是空值，不是空字符串

```python
if value is None:
    ...
```

判断 None 用 `is None`。

### import 会执行模块顶层代码

循环 import 常见。只为类型提示时用 `TYPE_CHECKING`：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.ctx import RunContext
```

### async 需要一路 await

```python
result = await runner.run(subtask, ctx)
```

## 17. 新人第一天建议阅读顺序

1. `docs/architecture_onboarding.md`（本文）
2. `docs/development_guide.md`：代码规范
3. `runtime/host.py` + `runtime/worker.py` + `runtime/wire.py`：看对象怎么装配
4. `agents/base.py`：核心数据结构
5. `agents/operator/vision/step.py`：截图-LLM-动作循环
6. `agents/operator/subtask_runner.py` + `resolvers/*`：快路径 + 慢路径切换
7. `agents/operator/recipe_operator.py`：快路径
8. `services/recipes/` + `services/vision/`：动作记忆与定位
9. `skills/rednote_*/`：领域包示例
10. `config/sections.py`：配置结构

## 18. 常见改动入口

| 改动 | 入口 |
|------|------|
| 新增 LLM 行为规则 | `prompts/operator/*.md` 或 `skills/<pack>/instructions.md` |
| 新增 skill tool | `skills/<pack>/__init__.py` 加 `@pack.tool` |
| 新增本地原子动作 | `tools/macos_chrome/actions.py` + `prompts/operator/action.md` |
| 新增跨模式原子动作 | 上面 + `tools/cloud_mobile/__init__.py::_ACTION_HANDLERS` |
| 切换执行模式 | 设置 `AGENT_RUNTIME_MODE`（+ `AGENTBAY_API_KEY` 云端），`curl /agent/runtime` 验证 |
| 新增第四种 Environment | `tools/xxx/` 实现 Protocol → `runtime/wire.py::build_env` 加分支 |
| 新增定时任务 | 实现 `ScheduledJob` → `Supervisor.__init__` 里 `registry.register(...)` |
| 新增可复用动作链 | 让 LLM 跑成功 → BehaviorSummarizer 自动挖候选 → 人工审查 + 补 template → 移入 `data/action_recipes/` 设 `enabled=true` |
| 新增 intent | 编辑 `data/intent_registry.json`，Planner/Miner 自动可见 |
| 新增 runtime 服务（所有 worker 共享） | `Host._build_brain_services()` 里装配 |
| 新增 worker 本地服务 | `Worker.__init__` 里装配，通过构造函数注入 |

## 19. 当前重点演进方向

1. 页面分类从 `unknown` 扩到可识别的所有小红书关键页面。
2. 为常用按钮积累 OpenCV template。
3. 给 recipe 增加更明确的 success check。
4. recipe 候选的自动升权 / 降权评估策略。
5. 建立 pytest smoke tests（parser、recipe matching、locator、dispatcher）。
