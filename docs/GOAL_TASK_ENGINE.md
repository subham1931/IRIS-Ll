# IRIS AI Assistant — Goal & Task Engine Developer Guide

## 1. Overview & Architecture

The **Goal & Task Engine** enables IRIS to handle multi-step, structured objectives with dependency DAG execution, automatic verification, transient failure retries, human-in-the-loop confirmation gates, and persistent state management across app restarts.

```
USER REQUEST
     │
     ▼
┌──────────────────┐
│  GoalDetector    │  (Identifies simple commands vs. multi-step goals)
└────────┬─────────┘
         │
         ▼ (Multi-Step Goal)
┌──────────────────┐
│   TaskPlanner    │  (Generates DAG ExecutionPlan with tool parameters)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   TaskExecutor   │◄────────┐
└────────┬─────────┘         │ (Retry / Next Step)
         │ (Runs Action)     │
         ▼                   │
┌──────────────────┐         │
│  ActionRegistry  │         │
└────────┬─────────┘         │
         │ (Tool Output)     │
         ▼                   │
┌──────────────────┐         │
│   TaskVerifier   ├─────────┘
└────────┬─────────┘
         │ (All Tasks Done)
         ▼
┌──────────────────┐
│  Memory Storage  │  (Concise episodic summary saved to long_term.json)
└──────────────────┘
```

---

## 2. Directory Structure

```
goal/
├── __init__.py         # Package exports
├── models.py           # Goal, Task, TaskResult, ExecutionPlan dataclasses & enums
├── detector.py         # Fast heuristics + LLM goal classifier
├── planner.py          # Structured DAG task planning with parameter templating
├── executor.py         # Async execution engine with retries and DAG dependency checking
├── verifier.py         # Semantic verification of tool outputs
├── persistence.py      # JSON state persistence in memory/goals.json
└── manager.py          # Central coordinator, UI rendering & memory integration

actions/
└── goal_action.py      # Bundled action exposing manage_goal tool to Gemini Live / UI
```

---

## 3. How to Create and Manage Goals

### 3.1 Creating a Goal Programmatically

```python
from goal import GoalManager

# Create goal instance
goal = goal_manager.create_goal(
    title="Research and Summarize AI Frameworks",
    description="Search for the latest 2026 AI framework releases, compare benchmarks, and write a summary file.",
    priority="high"
)

# Generate execution plan
goal_manager.plan_goal(goal)

# Execute asynchronously
await goal_manager.execute_goal_async(goal)
```

### 3.2 Creating Tasks and Adding Dependencies

When manually assembling a custom `ExecutionPlan`:

```python
from goal.models import Goal, Task, TaskStatus, ExecutionPlan

goal = Goal(id="goal_custom", title="Data Pipeline", description="Process data")

task_1 = Task(
    id="task_1",
    goal_id=goal.id,
    title="Fetch data from web",
    assigned_action="web_search",
    parameters={"mode": "research", "query": "latest python release"},
    dependencies=[]
)

task_2 = Task(
    id="task_2",
    goal_id=goal.id,
    title="Save findings to disk",
    assigned_action="file_controller",
    parameters={
        "action": "write",
        "path": "python_release.md",
        # Parameter substitution automatically injects output from task_1:
        "content": "{{task_1.output}}"
    },
    dependencies=["task_1"]  # Runs ONLY after task_1 completes
)

goal.tasks = [task_1, task_2]
goal.plan = ExecutionPlan(
    goal_id=goal.id,
    ordered_task_ids=["task_1", "task_2"],
    dependencies={"task_2": ["task_1"]}
)
```

---

## 4. Parameter Templating in DAGs

Tasks can consume previous task outputs by referencing placeholders in their parameters:
- `{{task_id.output}}`
- `{{task_id.result}}`

The `TaskExecutor` recursively resolves these placeholders right before executing the task.

---

## 5. Adding New Tools & Actions

To make a new tool discoverable by both IRIS and the Goal Engine:

1. Create a new file in `actions/my_tool.py`.
2. Define your handler function and export a module-level `TOOL` dictionary:

```python
# actions/my_tool.py

def my_custom_tool(parameters: dict, player=None, **kwargs) -> str:
    item = parameters.get("item", "")
    return f"Processed {item} successfully."

TOOL = {
    "name": "my_custom_tool",
    "description": "Performs custom data operations.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "item": {"type": "STRING", "description": "The item to process"}
        },
        "required": ["item"]
    },
    "handler": my_custom_tool,
}
```

The tool is automatically registered in `ActionRegistry`, exposed to `TaskPlanner`, and executable by `TaskExecutor`.

---

## 6. Human Confirmation Gates

Destructive or irreversible tasks (file deletion, git push, deployment, system shutdown) trigger confirmation:

1. `TaskVerifier` inspects the task action and output. If it requires approval (e.g. `[CONFIRMATION_PENDING]` or destructive action), it sets `requires_user_confirmation = True`.
2. `TaskExecutor` marks the task as `TaskStatus.WAITING_FOR_CONFIRMATION` and the goal as `GoalStatus.WAITING_FOR_USER`.
3. The on-screen confirmation banner is displayed via `core/confirm.py`.
4. When the user clicks **CONFIRM**, the callback is triggered, returning the task to `PENDING` to execute the approved step.

---

## 7. Adding Custom Verification Strategies

To add custom verification rules (e.g. validating JSON schemas, checking build exit codes):

In `goal/verifier.py`:

```python
class TaskVerifier:
    def verify(self, task: Task, raw_output: Any, raw_error: Optional[Exception | str] = None) -> VerificationResult:
        # Custom check for build output
        if task.assigned_action == "code_helper" and "SyntaxError" in str(raw_output):
            return VerificationResult(
                success=False,
                reason="Build generated syntax error in code output.",
                needs_retry=True,
                error_type=ErrorClassification.RECOVERABLE
            )
        ...
```

---

## 8. Goal Persistence & Crash Recovery

- Goals are persisted in `memory/goals.json`.
- When IRIS boots, `GoalManager.recover_incomplete_goals()` is automatically called in `main.py`.
- Interrupted or running goals are safely restored into the `PAUSED` state without auto-triggering unexpected side-effects.
- The user can say *"Resume goal"* or click resume to continue from where it left off.
