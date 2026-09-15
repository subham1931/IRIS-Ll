# IRIS AI Assistant — Goal & Task Engine Architecture Assessment

## 1. Current Execution Flow & Architecture Overview

IRIS currently operates primarily as a real-time reactive voice & text assistant powered by the **Gemini Live API** (`models/gemini-3.1-flash-live-preview` via WebSockets) and background task modules.

```
USER VOICE / TEXT INPUT
          │
          ▼
   Gemini Live API (WebSocket) / Local LLM
          │
     (Function Call)
          │
          ▼
   _execute_tool(fc) [main.py]
          │
   ┌──────┴────────────────────────┬─────────────────────────┐
   ▼                               ▼                         ▼
Built-in State Tools       ActionRegistry            PluginRegistry
(screen_process, etc.)     (actions/*.py)            (plugins/*.py)
   │                               │                         │
   └──────┬────────────────────────┴─────────────────────────┘
          ▼
  Tool Execution & Return String
          │
          ▼
  FunctionResponse → Gemini Live Session → Speaks response to User
```

### 1.1 Current Relevant Modules

1. **`main.py`**:
   - Establishes Gemini Live session, audio I/O, wake word loop, text command handling (`_on_text_command`), and tool execution dispatch (`_execute_tool`).
2. **`core/action_loader.py`**:
   - Discovers all bundled tools in `actions/*.py` exposing a `TOOL` dict (`name`, `description`, `parameters`, `handler`).
   - `ActionRegistry` holds verified actions and executes them via signature-introspected `run(name, parameters, ctx)`.
3. **`core/plugin_loader.py`**:
   - Discovers custom user plugins in `plugins/*.py` with isolated exception handling.
4. **`core/confirm.py`**:
   - Gate for irreversible actions (shutdown, restart, toggle_wifi). Shows HUD confirmation banner with CONFIRM / CANCEL buttons; actions return `[CONFIRMATION_PENDING]` without blocking conversation.
5. **`core/undo.py`**:
   - Central undo stack for reversible file and setting changes.
6. **`core/llm_client.py` & `actions/dev_agent.py`**:
   - Provides standalone LLM invocation (via Google GenAI client or local Ollama/OpenAI endpoints) used for structured planning and code generation.
7. **`memory/memory_manager.py`**:
   - Long-term memory store in `memory/long_term.json` divided into categories (`identity`, `preferences`, `projects`, `relationships`, `wishes`, `notes`).
8. **`ui.py`**:
   - PyQt6 HUD with real-time waveform, activity log (`write_log`), dynamic content panel (`show_content`), settings drawer, memory inspector, and confirmation banner hooks.

---

## 2. Problems and Limitations in Current Architecture

1. **Single-Step Reactive Execution**:
   - The LLM receives a prompt and executes at most one or two immediate tool calls per turn. Complex objectives (e.g. "Audit repo, update dependencies, run build, fix errors, and report status") rely entirely on the model remembering the goal across multiple interactive round-trips without a structured plan or dependency tracking.
2. **No Execution Plan or Dependency DAG**:
   - There is no formal abstraction for multi-step goals with predecessor/successor tasks, intermediate state passing, or rollback/recovery.
3. **No Systematic Result Verification**:
   - Tool return codes/strings are passed straight back to the model without verifying whether the underlying goal actually succeeded (e.g. a command exited cleanly with code 0 but the compiler threw semantic warnings or produced no build output).
4. **No Goal State Persistence or Resumption**:
   - If the application restarts or crashes midway through a multi-step workflow, all state is lost. There is no recovery mechanism for incomplete objectives.
5. **No Structured Pause / Resume / Cancel Controls**:
   - Users cannot pause an ongoing complex workflow, inspect its progress, resume it, or cancel pending steps.

---

## 3. Proposed Goal + Task Engine Architecture

The new Goal + Task Engine introduces a modular orchestration layer on top of IRIS's existing action/plugin system without rewriting existing handlers or breaking backward compatibility.

```
                  USER REQUEST
                       │
                       ▼
               ┌───────────────┐
               │ Goal Detector │
               └───────┬───────┘
                       │
         ┌─────────────┴─────────────┐
         │ is_goal = false           │ is_goal = true
         ▼                           ▼
  Direct Tool / Chat Flow     ┌──────────────┐
                              │ Task Planner │
                              └──────┬───────┘
                                     │
                             (Execution Plan DAG)
                                     │
                                     ▼
                              ┌──────────────┐
                    ┌────────►│ TaskExecutor │◄────────┐
                    │         └──────┬───────┘         │ (retry /
                    │                │                 │  next task)
                    │         (Execute Action)         │
                    │                ▼                 │
                    │         ┌──────────────┐         │
                    │         │Action/Plugin │         │
                    │         │   Registry   │         │
                    │         └──────┬───────┘         │
                    │                │                 │
                    │         (Task Output)            │
                    │                ▼                 │
                    │         ┌──────────────┐         │
                    │         │ TaskVerifier ├─────────┘
                    │         └──────┬───────┘
                    │                │
                    │         (Goal Complete)
                    │                ▼
                    │         ┌──────────────┐
                    │         │Memory Storage│
                    │         └──────────────┘
                    │
            ┌───────┴────────┐
            │  GoalManager   │ ◄─── Pause / Resume / Cancel / Persistence
            └────────────────┘
```

### 3.1 Modular Components (`goal/`)

1. **`goal/models.py`**:
   - Data models: `Goal`, `Task`, `TaskResult`, `ExecutionPlan`, `GoalStatus`, `TaskStatus`, `ErrorClassification`.
2. **`goal/detector.py`**:
   - `GoalDetector`: Fast heuristic classifier + LLM intent analyzer to distinguish single commands vs. multi-step goals.
3. **`goal/planner.py`**:
   - `TaskPlanner`: Generates structured execution plans with dependency resolution based on available tools from `ActionRegistry` and `PluginRegistry`.
4. **`goal/executor.py`**:
   - `TaskExecutor`: Asynchronous execution engine that resolves task DAGs, handles parameter substitution from previous task results, checks dependencies, manages retries, and yields execution control on confirmation gates.
5. **`goal/verifier.py`**:
   - `TaskVerifier`: Analyzes execution outputs against success criteria and detects recoverable failures or secondary action requirements.
6. **`goal/persistence.py`**:
   - `GoalStore`: Persists goals and execution state to `memory/goals.json`, handling safe recovery upon startup.
7. **`goal/manager.py`**:
   - `GoalManager`: Unified orchestrator coordinating detection, planning, execution, persistence, UI progress events, confirmation callbacks, and pause/resume/cancel workflows.

---

## 4. Integration Points in Existing Codebase

1. **`core/action_loader.py` & `core/plugin_loader.py`**:
   - The Goal Engine queries registered action/plugin definitions (`get_tool_declarations()`) to provide available capabilities to the planner, and invokes `ActionRegistry.run(...)` during execution.
2. **`core/confirm.py`**:
   - When a task requires confirmation (destructive file delete, git push, deployment, system changes), the executor calls `core.confirm.request(...)` and transitions the task into `WAITING_FOR_CONFIRMATION` and the goal into `WAITING_FOR_USER`.
3. **`memory/memory_manager.py`**:
   - Upon goal completion, `GoalManager` stores a clean, concise summary in long-term memory under `projects` or `notes`.
4. **`ui.py`**:
   - `GoalManager` posts live progress updates to `ui.show_content("GOAL: ...", markdown_progress)` and `ui.write_log("GOAL: ...")`.
5. **`main.py`**:
   - Exposes a new `manage_goal` tool to Gemini Live / Text command loop, and integrates `GoalManager` into startup recovery and voice command routing.

---

## 5. Files to Create and Modify

### New Files:
- `docs/GOAL_TASK_ARCHITECTURE.md` (this file)
- `docs/GOAL_TASK_ENGINE.md` (developer usage guide)
- `goal/__init__.py`
- `goal/models.py`
- `goal/detector.py`
- `goal/planner.py`
- `goal/executor.py`
- `goal/verifier.py`
- `goal/persistence.py`
- `goal/manager.py`
- `actions/goal_action.py` (exposing goal management to the tool registry)
- `tests/test_goal_engine.py` (comprehensive automated test suite)

### Modified Files:
- `main.py` (initialize GoalManager, integrate recovery, connect UI callbacks)
- `core/prompt.txt` (guide the model on when to create/delegate multi-step goals)
