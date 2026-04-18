# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python agent framework for autonomously operating GUIs (currently targeting Xiaohongshu/小红书) via vision-language models. The agent captures screenshots, reasons about them with a VLM (Gemini/Claude), and dispatches actions to a pluggable `Environment`.

Two orthogonal dimensions — **runtime mode** (where actions run) and **process topology** (how decision + session are deployed):

### Runtime mode (`agent.runtime.mode`, per worker)

| mode | environment | strategy |
|------|-------------|----------|
| `local_chrome` (default) | `MacOSChromeEnv` — PyAutoGUI + ImageGrab | `VisionActionStep` (own VLM) |
| `cloudmobile` | `CloudMobileEnv` — Aliyun Wuying CloudPhone (AgentBay SDK) | `VisionActionStep` (own VLM) |
| `agentbay` | `CloudMobileEnv` (shared session) | `AgentBayDelegateStrategy` (delegated to Aliyun `mobile_use` Agent, black-box) |

When `SESSION_SERVICE_URL` is set, `CloudMobileEnv` is replaced by `RemoteEnv` (HTTP proxy), and `AgentBayDelegateStrategy` by `RemoteDelegateStrategy`. The agent layer doesn't know or care.

### Process topology (`AGENT_TOPOLOGY`)

| topology | role | entry |
|----------|------|-------|
| `monolith` (default) | brain + session in one process | `uvicorn app:app` |
| `brain` | decision-only (Supervisor/Operator/Planner…); talks to Session Service over HTTP | `AGENT_TOPOLOGY=brain SESSION_SERVICE_URL=... uvicorn app:app` |
| `session` | cloud-phone session manager only (screenshot/action/delegate over HTTP) | `AGENT_TOPOLOGY=session AGENTBAY_API_KEY=... uvicorn app:app` |

Exactly one entry file (`app.py`). `app_brain.py` / `app_session.py` are retired.

## Development Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run (monolith, default)
uvicorn app:app --host 0.0.0.0 --port 6702 --reload

# Health check / debug mode / sync task
curl http://127.0.0.1:6702/health
curl -X POST http://127.0.0.1:6702/api/v1/agent/mode/debug
curl -X POST http://127.0.0.1:6702/api/v1/agent/actions/sync \
  -H 'Content-Type: application/json' \
  -d '{"user_goal": "打开小红书首页"}'

# Switch to cloud mode (no real session created until first task)
AGENT_RUNTIME_MODE=cloudmobile AGENTBAY_API_KEY=<key> uvicorn app:app --port 6702
curl http://127.0.0.1:6702/api/v1/agent/runtime   # inspect current mode/env/strategy

# Split topology: session node + brain node
AGENT_TOPOLOGY=session AGENTBAY_API_KEY=<key> uvicorn app:app --port 6710
AGENT_TOPOLOGY=brain SESSION_SERVICE_URL=http://session-host:6710 uvicorn app:app --port 6702

# Single-file syntax check (no test framework configured)
.venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in __import__('glob').glob('**/*.py', recursive=True) if '.venv' not in f]"

# Verify config + host boots
.venv/bin/python -c "import config; config.validate(); from runtime import Host; Host.boot(config.topology); print('OK')"
```

## Architecture

Six layers, single responsibility each:

| Layer | Path | Role |
|-------|------|------|
| **Agents** | `agents/` | Decision-making units (think + decide) |
| **Services** | `services/` | Stateful business logic shared across agents |
| **Tools** | `tools/` | Environment interaction (GUI / cloud phone / HTTP proxy / LLM) |
| **Skills** | `skills/` | Domain packs (instructions + callable tools) declared via `@pack.tool` |
| **Runtime** | `runtime/` | Host / Worker / RunContext / EventBus — the DI + lifecycle layer |
| **Bootstrap** | `bootstrap/` | Process-cold-start side effects (platform probe, Chrome install/launch) |
| **API** | `api/` | FastAPI routers, mounted per topology |
| **Utils** | `utils/` | Pure infrastructure (logging, parsing, fs, http, events) |

### Runtime layer (`runtime/`)

The runtime was redesigned around the topology split. The old `AppContainer` + `SessionAppContainer` + `WorkerPool` + `SessionWorker` are all gone.

- **`Host`** (`runtime/host.py`) — process singleton. Holds `EventBus`, `workers: dict[account_id, Worker]`, and in brain/monolith topologies also shared services (`llm`, `skills`, `intents`, `page_registry`, `page_matcher`, `page_classifier`, `page_context_cache`, `visual_locator`, `recipe_store`, `trace_recorder`, `behavior_summarizer`, `planner`). `Host.boot(topology)` is the single entry; `Host.current()` retrieves it.
- **`Worker`** (`runtime/worker.py`) — per-account execution unit.
  - Full worker (monolith/brain): holds `SessionManager` + `Environment` + `ActionDispatcher` + `VisionActionStep` + `RecipeOperator` + `SubtaskStrategy` + `SubtaskRunner` + `Operator` + `Strategist` + `Supervisor`.
  - Minimal worker (session-only, `minimal=True`): holds only `SessionManager` + `Environment` + display metadata.
  - `swap_runtime_mode(new_mode)` hot-swaps env + strategy without tearing down the supervisor.
- **`runtime/wire.py`** — stateless factories `build_env() / build_session() / build_strategy() / state_file_for()`. Branches on `agent.runtime.mode` and presence of `session_service_url` (remote). `Host` and `Worker` import these; nothing else should.
- **`RunContext`** / **`CancelScope`** (`runtime/ctx.py`) — explicit exec context with tree-wise cooperative cancellation. `ctx.cancel.raise_if_cancelled()` at safe points. `ctx.child()` derives a child scope that unlinks on context-manager exit (no infinite `children` growth).
- **`EventBus`** / **`Event`** (`runtime/events.py`) — typed pub/sub for supervisor / scheduler / SSE; `attach_log_bridge(bus)` relays logs to the bus for the dashboard.
- **`runtime/signals.py`** — SIGINT escalator (first Ctrl-C = graceful stop; repeated = hard kill). Installed only in brain-capable processes.

### Agent layer

| Agent | File | Responsibility |
|-------|------|---------------|
| `Supervisor` | `agents/supervisor/supervisor.py` | Thin orchestrator: mode transitions + task submission + scheduler driver. No business logic. |
| `ModeState` | `agents/supervisor/mode_state.py` | WAITING / PATROLLING / EXECUTING / DEBUG state machine. |
| `RunSlot` | `agents/supervisor/run_slot.py` | Single-slot task claim + preemption + drain. |
| `RunContextFactory` | `agents/supervisor/run_context_factory.py` | Builds `RunContext` with fresh `trace_id` + `CancelScope`. |
| `Scheduler` | `agents/supervisor/scheduler.py` | Time-sliced loop that picks + runs scheduled `Job`s. |
| `JobRegistry` + `ScheduledJob`s | `agents/supervisor/jobs/` | `PatrolJob`, `PostJob`, `SimpleGoalJob("dm"/"cr")`. Add a new scheduled task by implementing `ScheduledJob` and `registry.register()` — no supervisor edits. |
| `Operator` | `agents/operator/operator.py` | Top-level task orchestration: plan → execute subtasks → aggregate. |
| `SubtaskRunner` | `agents/operator/subtask_runner.py` | Runs a subtask through an ordered list of `SubtaskResolver`s, then notifies `SubtaskObserver`s. |
| `SubtaskResolver` | `agents/operator/resolver.py` + `resolvers/{recipe,strategy}.py` | `RecipeResolver` (recipe quickpath), `StrategyResolver` (delegates to injected `SubtaskStrategy`, applies `RetryPolicy`). |
| `RetryPolicy` / `StopPolicy` | `agents/operator/retry_policy.py` / `stop_policy.py` | Pluggable policies: `ResumeOnBudgetPolicy` (retry on step-budget), `StopOnFirstFailure`. |
| `SubtaskObserver` | `agents/operator/observer.py` | Side-effect hooks after subtask completion. `BehaviorSummarizerObserver` writes candidate recipes. |
| `SubtaskStrategy` | `agents/operator/strategy.py` | Protocol: `name + async run(subtask, ctx) -> TaskResult`. |
| `VisionActionStep` | `agents/operator/vision/step.py` | Own VLM loop. Split across the `vision/` subpackage: `config.py`, `step.py`, `history.py`, `prompt_builder.py`, `decision_parser.py`, `step_feedback.py`, `types.py`. |
| `AgentBayDelegateStrategy` | `agents/operator/agentbay_strategy.py` | Delegates whole subtask to Aliyun `mobile_use` Agent. |
| `ActionDispatcher` | `agents/operator/action_dispatcher.py` | Routes decisions to skill tools / atomic actions / finish. Atomic actions go through the injected `Environment`. |
| `RecipeOperator` | `agents/operator/recipe_operator.py` | Recipe-quickpath executor. Resolves locators via `VisualLocator`, runs steps via `Environment`. |
| `Planner` | `agents/planner/planner.py` | LLM-driven task decomposition. Split across `planner.py`, `prompt.py`, `parser.py`, `normalizer.py`, `config.py`. `build_default_planner()` is the wiring. |
| `Strategist` | `agents/strategist/strategist.py` | Content strategy (brainstorm + patrol/posting goals). Split across `brainstormer.py`, `generators/{patrol,post}.py`, `persona.py`, `context.py`, `renderer.py`, `selector.py`. |

All agents share the **typed contract** in `agents/base.py` (re-exports `Observation / Action / Decision / ActionOutcome / ActionResult` from `services/contracts.py`):
- `Task` / `SubTask` (with `intent`) / `Plan` / `TaskResult` — structured data flow
- `AgentError` hierarchy: `ControlSignal` (`CancelledError`, `StepBudgetExceededError`), `CapabilityError` (`PlannerError`, `StrategistError`), `UpstreamError` (`LlmError`, `DecisionParseError`), `InputError`

`agents/result_aggregation.py` centralises `TaskResult` roll-up (success/failure policy over sub-results).

### Service layer (`services/`)

- **`services/state.py`** — `AgentStateRepo` Protocol + `JsonFileStateRepo` / `InMemoryStateRepo`. `DEFAULT_STATE` is the single schema truth. `get()` always returns a complete dict.
- **`services/contracts.py`** — shared VLM data types (`Observation`, `Action`, `Decision`, `ActionOutcome`, `ActionResult`).
- **`services/intents.py`** — `IntentRegistry`. Shared intent vocabulary for Planner + RecipeMiner. Source of truth: `data/intent_registry.json`. `resolve()` normalises LLM output; `format_for_prompt()` injects the candidate list.
- **`services/skill_registry.py`** — thin façade over `skills.SkillRegistry` (kept for legacy import paths).
- **`services/recipes/`** — action-memory subsystem:
  - `model.py` — `ActionRecipe`, `RecipeStep`, `RecipeAttempt`
  - `repo.py` — `RecipeStore` (formal + trial tiers, matching, trial lifecycle auto-promote/demote)
  - `recorder.py` — `TraceRecorder` (append-only JSONL)
  - `miner.py` — `BehaviorSummarizer` (LLM side-channel: summarise successful traces → candidate recipes)
- **`services/vision/`** — page-recognition subsystem:
  - `types.py` — `PageLandmark`, `PageClassification`, `PageContext`, `BBox`, `LocateResult`
  - `locator.py` — `VisualLocator` (point + template)
  - `pages.py` — `PageRegistry`
  - `classifier.py` — `UnknownPageClassifier` / `LocalPageMatcher` / `LlmPageClassifier`
  - `cache.py` — `PageContextCache` (per-trace hash cache + optional background classification)
- **`services/knowledge.py`** — `harvest_knowledge(summary, trace_id, state)` extracts `[SHOT]/[TAG]/[MOOD]/…` from task summaries and writes to state. `get_evolution_context(...)` computes attention weights.
- **`services/history.py`** — task history initialisation (`history/index.jsonl` + `history/{trace_id}.md`).
- **`services/token_usage.py`** — token-accounting log writer under `logs/token_usage/`.
- **`services/waiting_queue.py`** — verification-code / callback waiting-room (moved from utils/).

### Tool layer (`tools/`)

Two façades — `Environment` (execution) and `LlmTool` (decision-time LLM).

- **`tools/environment.py`** — `Environment` Protocol: `capture(trace_id, include_cursor) -> (b64, mx, my)` + `perform(trace_id, action) -> {ok, message, finish}`. Plus `coerce_param()` for LLM-dirty-key tolerance. Consumers (`ActionDispatcher`, `VisionActionStep`, `RecipeOperator`) take it via constructor injection — never import concrete envs directly.
- **`tools/macos_chrome/`** — `MacOSChromeEnv` + `actions.py` (click/dblclick/move/scroll/drag/paste/copy/hotkey/wait, humanised Bezier + jitter) + `capture.py` + `coordinates.py` (Window + LLM-to-screen) + `humanize.py` + `cleanup.py` (close Chrome tabs).
- **`tools/cloud_mobile/`** — `CloudMobileEnv` + `SessionManager` (lazy session lifecycle, idle timeout, orphan cleanup) + `keymap.py` (LLM key names → Android keycodes).
- **`tools/remote/`** — `RemoteEnv` (HTTP proxy for `capture`/`perform`) + `RemoteDelegateStrategy` (HTTP proxy for `delegate-task`). Both used only when `SESSION_SERVICE_URL` is set (brain topology, or monolith forcing remote).
- **`tools/llm/`** — `LlmTool` (façade with `with_trace()`) + `caller.py` (retry, token logging, JSON/text/template modes) + `client.py` (OpenAI-compat HTTP). `MockLlmTool` for tests.

When **adding a new atomic action** that should work cross-mode: update `tools/macos_chrome/actions.py` *and* the `_ACTION_HANDLERS` map in `tools/cloud_mobile/__init__.py`. Mobile-irrelevant actions (e.g. `move` cursor) belong in `_NOOP_ACTIONS` and should return `ok=True` so the LLM chain isn't broken. Also update `prompts/operator/action.md`.

### Skills (`skills/`)

Skills are Python packages using a `@pack.tool` decorator, not YAML manifests:

```
skills/_lib/              # Pack / ToolContext / ToolOutcome / SkillRegistry / loader
skills/rednote_auth/
  __init__.py             # pack = Pack(...); @pack.tool def open_rednote_homepage(ctx): ...
  instructions.md         # LLM-facing domain instructions
skills/rednote_explorer/
skills/rednote_publish/
```

A skill declares its `supports=("local_chrome",)` tuple; tools are invoked through `SkillRegistry.invoke_tool(name, params, ToolContext(trace_id, ...))` and return a `ToolOutcome`. Instructions are scanned at boot; scripts lazy-load on first use.

Adding a skill: create a package dir, define `pack = Pack(name=..., description=..., supports=(...))`, decorate functions with `@pack.tool`, write `instructions.md`. No registry edits needed.

### Configuration (`config/`)

`config/` is now a package backed by pydantic:

- `config/sections.py` — `AppSettings` (root) + nested `SystemCfg` / `LlmCfg` / `AgentCfg`. Type-checked on import.
- `config/topology.py` — `Topology` enum + helpers (`has_brain`, `has_session`, `needs_local_chrome`, `serves_dashboard`).
- `config/_env.py` / `config/_chrome.py` — env readers and Chrome default-path probing.
- `config/__init__.py` — exposes `settings`, `topology`, `validate()`. `validate()` only checks **cross-section** constraints (pydantic handles type/structure).

Access is typed: `config.settings.agent.runtime.mode`, `config.settings.llm.clients["zenmux"].api_key`, `config.topology.has_brain`.

Environment-driven entries that matter most:

```text
AGENT_TOPOLOGY              monolith | brain | session          (default: monolith)
AGENT_RUNTIME_MODE          local_chrome | cloudmobile | agentbay (default: local_chrome)
AGENTBAY_API_KEY            required when mode != local_chrome (unless remote)
AGENTBAY_IMAGE_ID           default: mobile_latest
AGENTBAY_SCREENSHOT_FORMAT  jpeg|png (default: jpeg)
AGENTBAY_IDLE_RELEASE_TIMEOUT  seconds, default 600
SESSION_SERVICE_URL         if set → use RemoteEnv/RemoteDelegateStrategy
SESSION_SERVICE_API_KEY     bearer token for Session Service
AGENT_AUTO_CLEANUP_ORPHANS  default true; disable when multiple replicas share one API key
APP_ENV                     selects .{APP_ENV}.env file in non-Docker
```

### Multi-account / worker matrix

`config.settings.agent.accounts` is a list of per-account overrides (`id`, `display_name`, `mode`, `image_id`, `state_file`, `screenshot_format`). Empty list → single `default` worker (backwards compatible).

Each worker has its own state file (`data/accounts/{id}/agent_state.json` by default), its own cloud session, its own `Supervisor` + runtime mode. Shared across workers: `LlmTool`, `SkillRegistry`, `IntentRegistry`, `PageContextCache`, `VisualLocator`, `RecipeStore`, `TraceRecorder`, `BehaviorSummarizer`, `Planner`, `EventBus`.

Runtime CRUD: `POST /api/v1/agent/workers` / `DELETE /api/v1/agent/workers/{id}` (ephemeral, not persisted). Permanent accounts go in config.

### Prompt templates (`prompts/`)

All prompts are `.md` files loaded via `utils.prompt.load_prompt()`. Two subdirectories:
- `prompts/operator/` — `action.md`, `plan.md`, `sub_goal_constraint.md` (no frontmatter, embedded into system prompt)
- `prompts/agent/` — `brainstorm.md`, `patrol_goal.md`, `posting_goal_*.md` (with YAML frontmatter declaring `provider`/`model`/`temperature` for independent LLM calls)

No prompt content is hardcoded in Python files.

#### Prompt 变量注入约定

| 风格 | 语法 | 注入方式 | 适用场景 |
|------|------|----------|----------|
| **Python format** | `{VAR}` | `body.format(VAR=value)` | 模板中不含 JSON 花括号 |
| **占位符替换** | `@@VAR@@` | `body.replace("@@VAR@@", value)` | 模板中含 JSON 示例，避免 `.format()` 与 JSON `{}` 冲突 |

新增 prompt 时：若模板正文含 JSON 示例，使用 `@@VAR@@`；否则使用 `{VAR}`。

### Intent 注册表 (`data/intent_registry.json`)

Planner 和 RecipeMiner (BehaviorSummarizer) 的共享语义标签词表，是 intent 的唯一真相源。两端 LLM 调用时均注入此候选列表，输出的 intent 经 `IntentRegistry.resolve()` 归一化后用于 Recipe 精确匹配。新增 intent 只改 JSON，Python 自动可见。

### Execution flow

```
HTTP request
   ↓
api/v1/tasks.py  (resolve_worker(account_id) → worker.supervisor)
   ↓
Supervisor.submit_task(task, trace_id)
   ↓ RunSlot.preempt()  (cancels previous run, awaits drain)
   ↓ ModeState → EXECUTING
   ↓ RunContextFactory.create(trace_id)
   ↓
Operator.run(task, ctx)
   ↓ Planner.generate_plan(ctx, task.goal) → Plan (subtasks with intent)
   ↓   IntentRegistry.resolve() normalises each subtask.intent
   ↓ for each subtask:
   ↓   SubtaskRunner.run(subtask, ctx)
   ↓     for resolver in [RecipeResolver, StrategyResolver]:
   ↓       RecipeResolver → RecipeOperator.try_run()
   ↓         match subtask.intent + page_state → recipe hit
   ↓           execute steps via Environment (skip LLM) → resolved
   ↓         no match → skipped
   ↓       StrategyResolver → strategy.run() with RetryPolicy
   ↓         VisionActionStep (local_chrome / cloudmobile):
   ↓           loop:
   ↓             _observe → env.capture + PageContextCache.get_or_classify
   ↓             _think   → ctx.llm.call_json (with conversation history)
   ↓             _act     → ActionDispatcher.dispatch
   ↓                          → SkillRegistry.invoke_tool  (skill tools)
   ↓                          → env.perform                (atomic actions)
   ↓           finish → TaskResult.success
   ↓           budget exceeded → ResumeOnBudgetPolicy retries
   ↓         AgentBayDelegateStrategy (agentbay, local session):
   ↓           asyncio.to_thread(session.agent.mobile.execute_task…)
   ↓         RemoteDelegateStrategy (agentbay, remote via Session Service):
   ↓           httpx POST /api/v1/session/delegate-task
   ↓     BehaviorSummarizerObserver.on_complete()  (bg recipe mining)
   ↓
TaskResult  → aggregated by agents.result_aggregation
```

### Coordinate system

LLM produces normalised coordinates in 0-1000 range. Each environment converts internally:
- `MacOSChromeEnv` — `tools/macos_chrome/coordinates.py::Window.llm_to_screen()` uses the current Chrome window position (updated on each capture).
- `CloudMobileEnv` — `_llm_to_pixel()` uses screen dimensions cached from the most recent `beta_take_screenshot()` via `SessionManager.update_screen_size()`. The vision loop guarantees a capture precedes any action, so the cache is always populated.

### Cancellation

- `CancelScope` replaces the old `CancelToken` name. Tree-wise propagation, auto-unlink on context-manager exit.
- `ctx.cancel.raise_if_cancelled()` at safe points raises `CancelledError`.
- `Supervisor.request_stop(reason)` is sync (for signal handlers); `Supervisor.stop()` is async (graceful drain).
- `RunSlot.preempt()` cancels the previous handle and awaits its done event.

## API Surface

Mounted per topology (see `api/v1/__init__.py::register_routers`).

Brain-side (`tasks.py` / `workers.py` / `callbacks.py` / `usage.py` / `chrome_proxy.py`):
- `POST /api/v1/agent/actions` / `.../actions/sync` — async / sync task submit
- `POST /api/v1/agent/cancel` — cancel current task (per worker)
- `GET  /api/v1/agent/workers` / `POST /agent/workers` / `DELETE /agent/workers/{id}` — worker CRUD
- `GET  /api/v1/agent/status` / `GET /agent/runtime` — per-worker views
- `POST /api/v1/agent/runtime/mode` — hot-swap runtime mode
- `GET  /api/v1/agent/live-url` / `GET /agent/screen.jpg` — streaming / local screenshot
- `POST /api/v1/agent/session/release` — force-release cloud session
- `POST /api/v1/agent/mode/{debug,waiting}` / `POST /agent/patrol` / `POST /agent/scheduled/patrol-once` — supervisor controls
- `GET  /api/v1/usage/daily_stats` / `/usage/details` / `/usage/list_dates` — token accounting
- `GET  /api/v1/agent/chrome/{path}` / `WS /api/v1/agent/chrome/ws/{page_id}` — CDP proxy (local_chrome only useful)

Session-side (`sessions.py`):
- `POST /api/v1/session/acquire` / `.../release` / `GET /session/status` / `/session/url`
- `POST /api/v1/session/screenshot` / `.../action` — Environment over HTTP
- `POST /api/v1/session/delegate-task` — mobile_use Agent delegation
- `GET  /api/v1/session/workers` / `POST /session/workers` / `DELETE /session/workers/{id}`
- `GET  /api/v1/session/config` / `/session/health`

Shared: `GET /api/v1/events` (SSE from `events.py`), `GET /health`, `GET /dashboard`.

Every brain endpoint that talks about a worker accepts an optional `account_id`; omit for the default worker.

## Important Notes

- **OS dependency depends on mode**: `local_chrome` requires macOS + `PyAutoGUI` + `pyobjc` + accessibility permissions; `cloudmobile` / `agentbay` only need `wuying-agentbay-sdk` + API key.
- **Chrome launch is in `bootstrap/`** (only in monolith + `local_chrome`): `bootstrap.boot()` runs `chrome_install.ensure_installed()` + `chrome_launch.prepare()` + `chrome_client.start()`. Configurable via `CHROME_BINARY` and `CHROMEDRIVER_PATH`.
- **Cloud session is lazy**: constructing `CloudMobileEnv` / `SessionManager` does not allocate a cloud instance. The session is created on the first `capture()` / `perform()` / `acquire()` call. Inspect via `GET /api/v1/agent/runtime` (`session_active` field).
- **Orphan cleanup**: `Host` boots call `tools.cloud_mobile.cleanup_orphan_sessions()` unless `AGENT_AUTO_CLEANUP_ORPHANS=false`. Disable when multiple replicas share one API key, otherwise they'll kill each other's sessions.
- **Codebase language**: comments, prompts, log messages are Chinese; Python identifiers and types are English.
- **No test framework configured** — `MockLlmTool` + `InMemoryStateRepo` exist for future tests; no pytest setup yet.
- **State storage**: per-worker JSON at `data/accounts/{id}/agent_state.json` (or `data/agent_state.json` for the default worker). Schema in `services.state.DEFAULT_STATE` — extend there when adding fields.
