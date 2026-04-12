# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python agent framework for autonomously operating GUIs (currently targeting Xiaohongshu/小红书) via vision-language models. The agent captures screenshots, reasons about them with a VLM (Gemini/Claude), and executes mouse/keyboard actions through browser automation on macOS.

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
| `SubtaskRunner` | `agents/operator/subtask_runner.py` | Per-subtask execution + step-budget retry logic |
| `VisionActionStep` | `agents/operator/vision_action.py` | The visual loop: observe (screenshot) → think (LLM) → act (dispatch) |
| `ActionDispatcher` | `agents/operator/action_dispatcher.py` | Routes decisions to skill tools / atomic actions / finish |
| `Planner` | `agents/planner/planner.py` | LLM-driven task decomposition into `Plan` of `SubTask`s |
| `Strategist` | `agents/strategist/strategist.py` | Content strategy: brainstorm topics, generate patrol/posting goals |

All agents share the **typed contract** defined in `agents/base.py`:
- `Task` / `SubTask` / `Plan` / `TaskResult` — structured data flow (no `dict[str, Any]`)
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
- **`knowledge.py`** — `harvest_knowledge(summary, trace_id, state)` extracts `[SHOT]/[TAG]/[MOOD]/...` from task summaries and writes to state. `get_evolution_context(state, title_few_shots)` computes attention weights.
- **`history.py`** — Task history initialization (writes to `history/index.jsonl` and `history/{trace_id}.md`).

### Tool layer (`tools/`)

| Tool | Role |
|------|------|
| `actions.py` | GUI execution: click, dblclick, move, scroll, drag, paste, copy, hotkey, wait. All actions are humanized (Bezier mouse paths, jitter, pre/post pauses). |
| `screenshot.py` | Screen capture, cursor overlay, base64 encoding. Updates `screen.update_window()` with current Chrome window geometry. |
| `screen.py` | Coordinate conversion (LLM 0-1000 ↔ physical pixels) + window state. |
| `cleanup.py` | Chrome environment reset (close all tabs except a blank one). |
| `llm_caller.py` | Lower-level LLM caller with retry, token logging, JSON/text/template modes. |
| `llm_tool.py` | `LlmTool` class (DI'd into agents). `with_trace(trace_id)` derives a bound child. `MockLlmTool` available for testing. |

### Configuration (`config.py`)

Three top-level namespaces (`system`, `model`, `agent`). Validated at startup via `config.validate()` against `_REQUIRED_SCHEMA` — missing keys cause immediate `RuntimeError` instead of silent runtime failures. Long content (e.g. recruitment info) lives in external files under `prompts/`.

### Prompt templates (`prompts/`)

All prompts are `.md` files loaded via `utils.prompt_template.load_prompt_template()`. Two subdirectories:
- `prompts/operator/` — `action.md`, `plan.md`, `sub_goal_constraint.md` (no frontmatter, embedded into system prompt)
- `prompts/agent/` — `brainstorm.md`, `patrol_goal.md`, `posting_goal_*.md` (with YAML frontmatter declaring `provider`/`model`/`temperature` for independent LLM calls)

No prompt content is hardcoded in Python files.

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
   ↓ Planner.generate_plan(ctx, task.goal) → Plan
   ↓ for each subtask:
   ↓   SubtaskRunner.run(subtask, ctx)
   ↓     VisionActionStep.run(subtask, ctx)
   ↓       loop:
   ↓         _observe → screenshot
   ↓         _think → ctx.llm.call_json (with ConversationHistory)
   ↓         _act → ActionDispatcher.dispatch
   ↓                  → SkillRegistry.invoke_tool  (custom tools)
   ↓                  → tools.actions.execute_action  (atomic)
   ↓       on finish: TaskResult.success
   ↓       on budget: StepBudgetExceededError → SubtaskRunner retries
   ↓
TaskResult
```

For scheduled jobs (patrol/post/dm/cr), `agents/supervisor/scheduled_jobs.py` registers handlers in `SCHEDULED_JOB_HANDLERS` dict — new task types are added by appending to the dict, not by editing supervisor.

### Coordinate system

LLM produces normalized coordinates in 0-1000 range. `tools/screen.py::llm_to_screen()` converts to physical pixels using the current window position (updated by `screenshot.py::update_window()` on each capture).

### Cancellation

Supervisor uses `CancelToken` for cooperative tree-wise cancellation:
- `ctx.cancel.raise_if_cancelled()` at safe points raises `CancelledError`
- `supervisor._preempt_current()` cancels the old `ActiveRun` and `await`s its `done` event
- No more set-based `aborted_trace_ids` with delayed cleanup timers

## API Surface

- `POST /api/v1/agent/actions` — async task (returns trace_id immediately)
- `POST /api/v1/agent/actions/sync` — blocking task (returns full result)
- `GET /api/v1/agent/status` — current mode + active run + today's stats
- `POST /api/v1/agent/patrol` — toggle scheduler on/off
- `POST /api/v1/agent/mode/{debug,waiting}` — mode switching
- `POST /api/v1/agent/maintenance/trigger` — force-run a maintenance task
- `GET /api/v1/agent/chrome/{path}` — Chrome DevTools Protocol HTTP proxy
- `WS /api/v1/agent/chrome/ws/{page_id}` — Chrome DevTools Protocol WebSocket proxy

## Important Notes

- **macOS-only**: depends on `PyAutoGUI` + `pyobjc` + accessibility permissions for GUI control
- **Chrome must be launched** by `utils/init_functions/init_chrome_client.py` with `--remote-debugging-port=9222`. Chrome binary path is configurable via `CHROME_BINARY` and `CHROMEDRIVER_PATH` env vars
- **Codebase language**: comments, prompts, log messages are Chinese; Python identifiers and types are English
- **No test framework configured** — `MockLlmTool` and `InMemoryStateRepo` exist for future test infrastructure but no pytest setup
- **State storage**: agent state is a single JSON file at `data/agent_state.json`. The full schema is in `services/agent_state.DEFAULT_STATE` — extend there when adding new fields
