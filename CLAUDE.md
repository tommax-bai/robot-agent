# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python agent framework for autonomously operating GUIs (currently targeting Xiaohongshu/小红书) via vision-language models. The agent captures screenshots, reasons about them with a VLM (Gemini/Claude), and dispatches actions to a pluggable backend.

Three execution modes are supported via `agent.runtime.mode` (see "Execution backends and strategies" below):

| mode | environment | decision |
|------|-------------|----------|
| `local_chrome` (default) | macOS Chrome via PyAutoGUI | own VLM (`VisionActionStep`) |
| `cloud_vision` | Aliyun Wuying CloudPhone (Android, AgentBay SDK) | own VLM |
| `cloud_aliyun` | Aliyun Wuying CloudPhone | Aliyun `mobile_use` Agent (delegated, black-box) |

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run (development)
uvicorn app:app --host 0.0.0.0 --port 6702 --reload

# Health check / debug mode / sync task
curl http://127.0.0.1:6702/health
curl -X POST http://127.0.0.1:6702/api/v1/agent/mode/debug
curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "打开小红书首页"}'

# Single-file syntax check (no test framework configured)
.venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in __import__('glob').glob('**/*.py', recursive=True) if '.venv' not in f]"

# Verify config + container boots
.venv/bin/python -c "import config; config.validate(); from runtime.container import init_container; init_container(); print('OK')"

# Switch to cloud mode (no real session created until first task)
AGENT_RUNTIME_MODE=cloud_vision AGENTBAY_API_KEY=<key> uvicorn app:app --port 6702
curl http://127.0.0.1:6702/api/v1/agent/runtime   # inspect current mode/backend/strategy
```

## Architecture

The codebase is divided into 5 layers, each with a single responsibility:

| Layer | Path | Role |
|-------|------|------|
| **Agents** | `agents/` | Decision-making units (think + decide) |
| **Services** | `services/` | Stateful business logic shared across agents |
| **Tools** | `tools/` | Environment interaction capabilities |
| **Runtime** | `runtime/` | Cross-cutting context + DI container |
| **Utils** | `utils/` | Pure infrastructure (logging, parsing, config loading) |

### Agent layer

| Agent | File | Responsibility |
|-------|------|---------------|
| `Supervisor` | `agents/supervisor/supervisor.py` | Mode management (WAITING/PATROLLING/EXECUTING/DEBUG), task preemption, schedule loop driver, status queries |
| `Operator` | `agents/operator/operator.py` | Top-level task orchestration: plan → execute subtasks |
| `SubtaskRunner` | `agents/operator/subtask_runner.py` | Per-subtask execution + step-budget retry logic; delegates to a `SubtaskStrategy` |
| `SubtaskStrategy` | `agents/operator/strategy.py` | Protocol: `name + async run(subtask, ctx) -> TaskResult`. Two impls below. |
| `VisionActionStep` | `agents/operator/vision_action.py` | Strategy impl — own VLM visual loop: observe (screenshot) → think (LLM) → act (dispatch). Used by `local_chrome` & `cloud_vision`. |
| `AliyunMobileAgentStrategy` | `agents/operator/aliyun_strategy.py` | Strategy impl — delegates the entire subtask to Aliyun `session.agent.mobile_use.execute_task_and_wait`. Used by `cloud_aliyun`. |
| `ActionDispatcher` | `agents/operator/action_dispatcher.py` | Routes decisions to skill tools / atomic actions / finish. Atomic actions go through the injected `ActionBackend`. |
| `Planner` | `agents/planner/planner.py` | LLM-driven task decomposition into `Plan` of `SubTask`s |
| `Strategist` | `agents/strategist/strategist.py` | Content strategy: brainstorm topics, generate patrol/posting goals |

All agents share the **typed contract** defined in `agents/base.py`:
- `Task` / `SubTask` (with `intent` field) / `Plan` / `TaskResult` — structured data flow (no `dict[str, Any]`)
- `Decision` / `Action` / `Observation` / `ActionOutcome` / `ActionResult` — vision-loop data
- `AgentError` hierarchy — `CancelledError`, `LlmError`, `PlannerError`, `StrategistError`, `StepBudgetExceededError`, `DecisionParseError`, `ToolNotFoundError`

`Decision.parse()` centralizes all LLM dirty-output cleanup (4 output shapes, key normalization, coordinate key fallback).

### Runtime layer (`runtime/`)

- **`AppContainer`** (`runtime/container.py`) — Dependency injection container. Constructed once in `app.py` lifespan, wires all agents/services/tools together.
- **`RunContext`** (`runtime/context.py`) — Explicit execution context passed through every agent call. Holds `trace_id`, `cancel` token, `state` repo, `llm` tool. Use `ctx.child()` to derive sub-contexts; cancel propagates tree-wise.
- **`CancelToken`** — Cooperative cancellation with tree-wise propagation. Replaces the old `aborted_trace_ids` set. Check via `ctx.cancel.raise_if_cancelled()`.
- **`ConversationHistory`** — Per-task conversation buffer for the vision loop. Injected into `VisionActionStep`, replaces the old global `runtime/context.agent.messages` dict.

### Service layer (`services/`)

- **`AgentStateRepo`** (`services/agent_state.py`) — Protocol with `JsonFileStateRepo` (production) and `InMemoryStateRepo` (test). State has a `DEFAULT_STATE` schema; `get()` always returns a complete dict.
- **`SkillRegistry`** (`services/skill_registry.py`) — Replaces the old `SkillLoader`. Lazy-loads scripts (manifest scanned at boot, scripts loaded on first use). Tool calls go through `invoke_tool(name, params, trace_id)`.
- **`IntentRegistry`** (`services/intent_registry.py`) — Planner 和 BehaviorSummarizer 的共享 intent 词表。从 `data/intent_registry.json` 加载，提供 `resolve()` 归一化和 `format_for_prompt()` 注入。是 intent 的唯一真相源。
- **`knowledge.py`** — `harvest_knowledge(summary, trace_id, state)` extracts `[SHOT]/[TAG]/[MOOD]/...` from task summaries and writes to state. `get_evolution_context(state, title_few_shots)` computes attention weights.
- **`history.py`** — Task history initialization (writes to `history/index.jsonl` and `history/{trace_id}.md`).

### Tool layer (`tools/`)

| Tool | Role |
|------|------|
| `backends/__init__.py` | `ActionBackend` Protocol (`screenshot` + `execute_action`) + re-exports of impls. |
| `backends/macos_chrome.py` | `MacOSChromeBackend` — thin wrapper delegating to `tools.actions` / `tools.screenshot`. Used by `local_chrome` mode. |
| `backends/agentbay.py` | `AgentBayBackend` — Aliyun Wuying CloudPhone session. Lazy session creation via `ensure_session(trace_id)` (no spend until first call). Used by `cloud_vision` and `cloud_aliyun`. |
| `actions.py` | macOS atomic GUI execution: click, dblclick, move, scroll, drag, paste, copy, hotkey, wait. Humanized (Bezier mouse paths, jitter, pre/post pauses). **Implementation detail of `MacOSChromeBackend`** — agents don't import this directly. |
| `screenshot.py` | macOS screen capture (ImageGrab + pywinctl + CDP window matching). Updates `screen.update_window()`. **Implementation detail of `MacOSChromeBackend`**. |
| `screen.py` | Coordinate conversion (LLM 0-1000 ↔ physical pixels) + window state. **Implementation detail of `MacOSChromeBackend`**. |
| `cleanup.py` | Chrome environment reset (close all tabs except a blank one). |
| `llm_caller.py` | Lower-level LLM caller with retry, token logging, JSON/text/template modes. |
| `llm_tool.py` | `LlmTool` class (DI'd into agents). `with_trace(trace_id)` derives a bound child. `MockLlmTool` available for testing. |

### Execution backends and strategies

The agent layer is decoupled from the execution environment via two orthogonal abstractions:

- **`ActionBackend`** (`tools/backends/__init__.py`) — *where* atomic actions run. Methods: `screenshot(trace_id, include_cursor) -> (b64, mx, my)` and `execute_action(trace_id, {method, params, finish}) -> {ok, message, finish, ...}`. Three consumers (`ActionDispatcher`, `VisionActionStep`, `RecipeOperator`) take it via constructor injection — they never import `tools.actions` / `tools.screenshot` directly.
- **`SubtaskStrategy`** (`agents/operator/strategy.py`) — *how* a subtask becomes a `TaskResult`. `SubtaskRunner` always tries `RecipeOperator` first (mode-agnostic), then delegates to the injected strategy.

`runtime.container._build_backend()` and `_build_strategy()` branch on `agent.runtime.mode`:

| mode | backend | strategy |
|------|---------|----------|
| `local_chrome` | `MacOSChromeBackend` | `VisionActionStep` |
| `cloud_vision` | `AgentBayBackend` | `VisionActionStep` |
| `cloud_aliyun` | `AgentBayBackend` (shared session) | `AliyunMobileAgentStrategy` |

Recipes execute through the backend regardless of mode — a recipe authored on macOS can replay on a cloud phone if its `locator` resolves.

When **adding a new atomic action** that should work cross-mode, you must update both backends (`tools/actions.py` for macOS, the `match` block in `tools/backends/agentbay.py` for AgentBay). Mobile-irrelevant actions (e.g. `move` cursor) should return `ok=True` with a no-op note rather than failing, so the LLM chain isn't broken.

### Configuration (`config.py`)

Three top-level namespaces (`system`, `model`, `agent`). `agent` 下按职责分为 `persona`（人设身份）、`schedule`（调度约束）、`storage`（持久化路径）、`runtime`（执行模式 + AgentBay 凭证）、`planner`/`operator`/`page_classifier`/`behavior_summarizer`（模型配置）等子命名空间。Validated at startup via `config.validate()` against `_REQUIRED_SCHEMA` — missing keys cause immediate `RuntimeError` instead of silent runtime failures. Long content (e.g. recruitment info) lives in external files under `prompts/`.

`agent.runtime` controls execution mode and is fully env-driven:

```text
AGENT_RUNTIME_MODE          local_chrome | cloud_vision | cloud_aliyun   (default: local_chrome)
AGENTBAY_API_KEY            required when mode != local_chrome
AGENTBAY_IMAGE_ID           default: mobile_latest
AGENTBAY_SCREENSHOT_FORMAT  default: jpeg (jpeg|png)
```

### Prompt templates (`prompts/`)

All prompts are `.md` files loaded via `utils.prompt_template.load_prompt_template()`. Two subdirectories:
- `prompts/operator/` — `action.md`, `plan.md`, `sub_goal_constraint.md` (no frontmatter, embedded into system prompt)
- `prompts/agent/` — `brainstorm.md`, `patrol_goal.md`, `posting_goal_*.md` (with YAML frontmatter declaring `provider`/`model`/`temperature` for independent LLM calls)

No prompt content is hardcoded in Python files.

#### Prompt 变量注入约定

项目中存在两种模板变量风格，各有适用场景：

| 风格 | 语法 | 注入方式 | 适用场景 |
|------|------|----------|----------|
| **Python format** | `{VAR}` | `body.format(VAR=value)` | 模板中不含 JSON 花括号（`plan.md`, `action.md`, `sub_goal_constraint.md`, strategist 模板） |
| **占位符替换** | `@@VAR@@` | `body.replace("@@VAR@@", value)` | 模板中含 JSON 示例（`page_classifier.md`, `behavior_summarizer.md`），避免 `.format()` 与 JSON `{}` 冲突 |

新增 prompt 时：若模板正文含 JSON 示例，使用 `@@VAR@@`；否则使用 `{VAR}`。

### Intent 注册表 (`data/intent_registry.json`)

Planner 和 BehaviorSummarizer 的共享语义标签词表，是 intent 的唯一真相源。两端 LLM 调用时均注入此候选列表，输出的 intent 经 `IntentRegistry.resolve()` 归一化后用于 Recipe 精确匹配。新增 intent 后两端自动可见，无需改动 Python 代码。

### Execution flow

```
HTTP request
   ↓
api/v1/route/agent.py  (uses get_container().supervisor)
   ↓
Supervisor.execute_task(task)
   ↓ preempts current run via cancel token
   ↓ claims new ActiveRun, derives RunContext
   ↓
Operator.run(task, ctx)
   ↓ Planner.generate_plan(ctx, task.goal) → Plan (subtasks with intent)
   ↓   IntentRegistry.resolve() normalizes each subtask.intent
   ↓ for each subtask:
   ↓   SubtaskRunner.run(subtask, ctx)
   ↓     RecipeOperator.try_run() — match by subtask.intent + page_state
   ↓       hit → execute recipe steps via backend (skip LLM) → done
   ↓       miss → fall through to strategy
   ↓     strategy.run(subtask, ctx)   # injected per agent.runtime.mode
   ↓       VisionActionStep (local_chrome / cloud_vision):
   ↓         loop:
   ↓           _observe → backend.screenshot + PageContextCache.get_or_classify()
   ↓           _think   → ctx.llm.call_json (with ConversationHistory)
   ↓           _act     → ActionDispatcher.dispatch
   ↓                        → SkillRegistry.invoke_tool   (custom tools)
   ↓                        → backend.execute_action      (atomic)
   ↓         on finish: TaskResult.success
   ↓         on budget: StepBudgetExceededError → SubtaskRunner retries
   ↓       AliyunMobileAgentStrategy (cloud_aliyun):
   ↓         asyncio.to_thread(session.agent.mobile_use.execute_task_and_wait, goal, timeout)
   ↓         map result.success / result / error_message → TaskResult
   ↓
TaskResult
```

For scheduled jobs (patrol/post/dm/cr), `agents/supervisor/scheduled_jobs.py` registers handlers in `SCHEDULED_JOB_HANDLERS` dict — new task types are added by appending to the dict, not by editing supervisor.

### Coordinate system

LLM produces normalized coordinates in 0-1000 range. Each backend converts internally:
- `MacOSChromeBackend` — `tools/screen.py::llm_to_screen()` uses the current Chrome window position (updated by `screenshot.py::update_window()` each capture).
- `AgentBayBackend` — `_llm_to_pixel()` uses screen dimensions cached from the most recent `beta_take_screenshot()` (`_screen_w / _screen_h`). The vision loop guarantees a screenshot precedes any action, so the cache is always populated.

### Cancellation

Supervisor uses `CancelToken` for cooperative tree-wise cancellation:
- `ctx.cancel.raise_if_cancelled()` at safe points raises `CancelledError`
- `supervisor._preempt_current()` cancels the old `ActiveRun` and `await`s its `done` event
- No more set-based `aborted_trace_ids` with delayed cleanup timers

## API Surface

- `POST /api/v1/agent/actions` — async task (returns trace_id immediately)
- `POST /api/v1/agent/actions/sync` — blocking task (returns full result)
- `GET /api/v1/agent/status` — current mode + active run + today's stats
- `GET /api/v1/agent/runtime` — runtime introspection: `agent.runtime.mode`, backend class, strategy class, AgentBay session activity (no session creation)
- `POST /api/v1/agent/patrol` — toggle scheduler on/off
- `POST /api/v1/agent/mode/{debug,waiting}` — supervisor mode switching (orthogonal to `agent.runtime.mode`)
- `GET /api/v1/agent/chrome/{path}` — Chrome DevTools Protocol HTTP proxy
- `WS /api/v1/agent/chrome/ws/{page_id}` — Chrome DevTools Protocol WebSocket proxy

## Important Notes

- **OS dependency depends on mode**: `local_chrome` requires macOS + `PyAutoGUI` + `pyobjc` + accessibility permissions; `cloud_vision` / `cloud_aliyun` only need `wuying-agentbay-sdk` and an API key — runnable on any OS.
- **Chrome must be launched** (only in `local_chrome` mode) by `utils/init_functions/init_chrome_client.py` with `--remote-debugging-port=9222`. Chrome binary path is configurable via `CHROME_BINARY` and `CHROMEDRIVER_PATH` env vars.
- **AgentBay session is lazy**: constructing `AgentBayBackend` does not allocate a cloud instance. The session is created on the first `screenshot()` / `execute_action()` / `ensure_session()` call. Inspect via `GET /api/v1/agent/runtime` (`session_active` field).
- **Codebase language**: comments, prompts, log messages are Chinese; Python identifiers and types are English
- **No test framework configured** — `MockLlmTool` and `InMemoryStateRepo` exist for future test infrastructure but no pytest setup
- **State storage**: agent state is a single JSON file at `data/agent_state.json`. The full schema is in `services/agent_state.DEFAULT_STATE` — extend there when adding new fields
