"""
goal/executor.py — Task Execution Engine with DAG dependency handling and retry loop.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from goal.models import (
    ErrorClassification,
    Goal,
    GoalStatus,
    Task,
    TaskResult,
    TaskStatus,
)
from goal.verifier import TaskVerifier


def _substitute_params(params: Any, context_results: Dict[str, Any]) -> Any:
    """Recursively replace {{task_id.output}} or {{task_id.result}} in parameters."""
    if isinstance(params, str):
        val = params
        # Match {{task_id.output}} or {{task_id.result}}
        matches = re.findall(r"\{\{([a-zA-Z0-9_\-]+)\.(output|result)\}\}", val)
        for task_id, key in matches:
            replacement = context_results.get(task_id)
            if replacement is not None:
                rep_str = str(replacement)
                placeholder = f"{{{{{task_id}.{key}}}}}"
                if val == placeholder:
                    return replacement
                val = val.replace(placeholder, rep_str)
        return val
    elif isinstance(params, dict):
        return {k: _substitute_params(v, context_results) for k, v in params.items()}
    elif isinstance(params, list):
        return [_substitute_params(item, context_results) for item in params]
    return params


class TaskExecutor:
    def __init__(
        self,
        action_runner: Callable[[str, dict, dict], Any],
        verifier: Optional[TaskVerifier] = None,
        progress_callback: Optional[Callable[[Goal, Task, str], None]] = None,
        confirmation_request_fn: Optional[Callable[[str, str, str, Callable], str]] = None,
    ):
        self._action_runner = action_runner
        self._verifier = verifier or TaskVerifier()
        self._progress_callback = progress_callback
        self._confirmation_request_fn = confirmation_request_fn

    def get_next_executable_task(self, goal: Goal) -> Optional[Task]:
        """Find the next pending task whose dependencies are all satisfied."""
        completed_task_ids = {
            t.id for t in goal.tasks if t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
        }
        failed_task_ids = {
            t.id for t in goal.tasks if t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED)
        }

        for task in goal.tasks:
            if task.status != TaskStatus.PENDING:
                continue

            # Check if any dependency has failed
            if any(dep in failed_task_ids for dep in task.dependencies):
                task.status = TaskStatus.BLOCKED
                task.error = "A required dependency failed or was blocked."
                self._notify(goal, task, "task_blocked")
                continue

            # Check if all dependencies are satisfied
            if all(dep in completed_task_ids for dep in task.dependencies):
                return task

        return None

    def execute_task(self, goal: Goal, task: Task, ctx: Optional[dict] = None) -> TaskResult:
        """Synchronously execute a single task, verify the result, and update state."""
        ctx = ctx or {}
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now().isoformat()
        goal.current_task_id = task.id
        self._notify(goal, task, "task_started")

        # Collect previous task outputs for variable substitution
        context_results = {
            t.id: t.result for t in goal.tasks if t.status == TaskStatus.COMPLETED and t.result is not None
        }
        resolved_params = _substitute_params(task.parameters, context_results)

        raw_output = None
        raw_error = None
        try:
            raw_output = self._action_runner(task.assigned_action, resolved_params, ctx)
        except Exception as e:
            raw_error = e

        # Verify result
        verification = self._verifier.verify(task, raw_output, raw_error)

        if verification.requires_user_confirmation:
            task.status = TaskStatus.WAITING_FOR_CONFIRMATION
            goal.status = GoalStatus.WAITING_FOR_USER
            self._notify(goal, task, "confirmation_requested")

            # If a confirmation callback is available, invoke it
            if self._confirmation_request_fn and verification.confirmation_details:
                action_name = verification.confirmation_details.get("action", task.assigned_action)
                title = f"Confirm Goal Action: {task.title}"
                detail = f"Action '{action_name}' requires approval to proceed with goal '{goal.title}'."

                def _on_confirm_cb():
                    # Resume execution of this task with confirmation bypassed
                    task.status = TaskStatus.PENDING
                    goal.status = GoalStatus.RUNNING

                self._confirmation_request_fn(
                    key=f"goal_{goal.id}_{task.id}",
                    title=title,
                    detail=detail,
                    run=_on_confirm_cb,
                )

            return TaskResult(
                success=False,
                output=raw_output,
                error=verification.reason,
                requires_confirmation=True,
                confirmation_details=verification.confirmation_details,
            )

        if verification.success:
            task.status = TaskStatus.COMPLETED
            task.result = raw_output
            task.completed_at = datetime.now().isoformat()
            task.error = None
            goal.calculate_progress()
            self._notify(goal, task, "task_completed")
            return TaskResult(success=True, output=raw_output)

        # Handle failure & retries
        if verification.needs_retry and task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = TaskStatus.PENDING
            task.error = f"Attempt {task.retry_count} failed: {verification.reason}"
            self._notify(goal, task, "task_retried")
            return TaskResult(
                success=False,
                output=raw_output,
                error=verification.reason,
                recoverable=True,
            )

        # Permanent failure
        task.status = TaskStatus.FAILED
        task.error = verification.reason
        task.completed_at = datetime.now().isoformat()
        goal.calculate_progress()
        self._notify(goal, task, "task_failed")
        return TaskResult(
            success=False,
            output=raw_output,
            error=verification.reason,
            recoverable=False,
        )

    async def run_goal_async(
        self,
        goal: Goal,
        ctx: Optional[dict] = None,
        max_steps: int = 50,
    ) -> Goal:
        """Run the goal execution loop asynchronously until completion, pause, or failure."""
        ctx = ctx or {}
        goal.status = GoalStatus.RUNNING
        self._notify(goal, None, "goal_started")

        steps = 0
        while steps < max_steps:
            if goal.status in (GoalStatus.PAUSED, GoalStatus.CANCELLED, GoalStatus.WAITING_FOR_USER):
                break

            task = self.get_next_executable_task(goal)
            if task is None:
                # Check if all tasks completed or if any failed/blocked
                all_done = all(
                    t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in goal.tasks
                )
                any_failed = any(
                    t.status in (TaskStatus.FAILED, TaskStatus.BLOCKED) for t in goal.tasks
                )

                if all_done:
                    goal.status = GoalStatus.COMPLETED
                    goal.current_task_id = None
                    goal.calculate_progress()
                    self._notify(goal, None, "goal_completed")
                elif any_failed:
                    goal.status = GoalStatus.FAILED
                    goal.error = "One or more tasks failed or were blocked."
                    self._notify(goal, None, "goal_failed")
                break

            # Execute the task
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(None, lambda: self.execute_task(goal, task, ctx))
            steps += 1

            if res.requires_confirmation or goal.status == GoalStatus.WAITING_FOR_USER:
                break

            # Brief pause between tasks for responsiveness
            await asyncio.sleep(0.05)

        goal.calculate_progress()
        goal.updated_at = datetime.now().isoformat()
        return goal

    def _notify(self, goal: Goal, task: Optional[Task], event: str) -> None:
        if self._progress_callback:
            try:
                self._progress_callback(goal, task, event)
            except Exception as e:
                print(f"[Executor] ⚠️ Progress callback error: {e}")
