"""
goal/manager.py — Central Goal and Task Engine Manager for IRIS.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from goal.detector import GoalDetectionResult, GoalDetector
from goal.executor import TaskExecutor
from goal.models import Goal, GoalStatus, Task, TaskStatus
from goal.persistence import GoalStore
from goal.planner import TaskPlanner
from goal.verifier import TaskVerifier


class GoalManager:
    def __init__(
        self,
        action_runner: Callable[[str, dict, dict], Any],
        get_tools_fn: Callable[[], List[Dict[str, Any]]],
        get_memory_fn: Optional[Callable[[], str]] = None,
        save_memory_fn: Optional[Callable[[dict], None]] = None,
        ui_show_content: Optional[Callable[[str, str], None]] = None,
        ui_write_log: Optional[Callable[[str], None]] = None,
        ui_speak: Optional[Callable[[str], None]] = None,
        confirmation_fn: Optional[Callable[[str, str, str, Callable], str]] = None,
        api_key_provider: Optional[Callable[[], str]] = None,
        store: Optional[GoalStore] = None,
    ):
        self._action_runner = action_runner
        self._get_tools_fn = get_tools_fn
        self._get_memory_fn = get_memory_fn
        self._save_memory_fn = save_memory_fn
        self._ui_show_content = ui_show_content
        self._ui_write_log = ui_write_log
        self._ui_speak = ui_speak
        self._confirmation_fn = confirmation_fn
        self._api_key_provider = api_key_provider

        self._store = store or GoalStore()
        self._detector = GoalDetector(api_key_provider=api_key_provider)
        self._planner = TaskPlanner(api_key_provider=api_key_provider)
        self._verifier = TaskVerifier()
        self._executor = TaskExecutor(
            action_runner=action_runner,
            verifier=self._verifier,
            progress_callback=self._on_progress_event,
            confirmation_request_fn=confirmation_fn,
        )

        self._active_goal: Optional[Goal] = None
        self._running_task_future: Optional[asyncio.Task] = None

    def detect_intent(self, text: str, context: Optional[dict] = None) -> GoalDetectionResult:
        """Evaluate if user request is a multi-step goal."""
        return self._detector.detect(text, context)

    def create_goal(
        self,
        title: str,
        description: str,
        priority: str = "normal",
        metadata: Optional[dict] = None,
    ) -> Goal:
        """Create and persist a new goal instance."""
        gid = f"goal_{uuid.uuid4().hex[:8]}"
        goal = Goal(
            id=gid,
            title=title,
            description=description,
            status=GoalStatus.PENDING,
            priority=priority,
            metadata=metadata or {},
        )
        self._store.save_goal(goal)
        self._active_goal = goal
        self._log(f"GOAL: Created '{title}' [{gid}]")
        return goal

    def plan_goal(self, goal: Goal, conversation_context: Optional[str] = None) -> Goal:
        """Generate tasks and an execution plan for the goal."""
        tools = self._get_tools_fn() if callable(self._get_tools_fn) else []
        mem_str = self._get_memory_fn() if callable(self._get_memory_fn) else ""

        self._planner.plan_goal(
            goal=goal,
            available_tools=tools,
            memory_context=mem_str,
            conversation_context=conversation_context,
        )
        self._store.save_goal(goal)
        self._log(f"GOAL: Planned {len(goal.tasks)} tasks for '{goal.title}'")
        self._update_ui_progress(goal)
        return goal

    async def execute_goal_async(self, goal: Goal, ctx: Optional[dict] = None) -> Goal:
        """Start async execution of the planned goal."""
        self._active_goal = goal
        self._store.save_goal(goal)
        self._update_ui_progress(goal)

        try:
            executed_goal = await self._executor.run_goal_async(goal, ctx=ctx)
            self._store.save_goal(executed_goal)
            self._update_ui_progress(executed_goal)

            if executed_goal.status == GoalStatus.COMPLETED:
                self._record_completion_memory(executed_goal)

            return executed_goal
        except Exception as e:
            goal.status = GoalStatus.FAILED
            goal.error = str(e)
            self._store.save_goal(goal)
            self._update_ui_progress(goal)
            self._log(f"GOAL: Execution error on '{goal.title}': {e}")
            return goal

    async def handle_request(
        self,
        user_request: str,
        conversation_context: Optional[str] = None,
        ctx: Optional[dict] = None,
    ) -> Optional[Goal]:
        """Convenience method: detect, plan, and execute if request is a goal."""
        detection = self.detect_intent(user_request)
        if not detection.is_goal:
            return None

        # Speak or log goal acknowledgment
        if self._ui_speak:
            self._ui_speak(f"Goal created: {detection.goal_title}. Planning tasks now.")

        goal = self.create_goal(
            title=detection.goal_title,
            description=detection.goal_description,
        )
        self.plan_goal(goal, conversation_context=conversation_context)
        return await self.execute_goal_async(goal, ctx=ctx)

    def pause_goal(self, goal_id: Optional[str] = None) -> Optional[Goal]:
        """Pause a running or pending goal."""
        goal = self._resolve_goal(goal_id)
        if not goal:
            return None
        if goal.status in (GoalStatus.RUNNING, GoalStatus.PENDING):
            goal.status = GoalStatus.PAUSED
            self._store.save_goal(goal)
            self._log(f"GOAL: Paused '{goal.title}'")
            self._update_ui_progress(goal)
        return goal

    def resume_goal(self, goal_id: Optional[str] = None, ctx: Optional[dict] = None) -> Optional[Goal]:
        """Resume a paused goal."""
        goal = self._resolve_goal(goal_id)
        if not goal:
            return None
        if goal.status in (GoalStatus.PAUSED, GoalStatus.WAITING_FOR_USER):
            goal.status = GoalStatus.RUNNING
            self._store.save_goal(goal)
            self._log(f"GOAL: Resumed '{goal.title}'")
            self._update_ui_progress(goal)
            # Spawn background execution if event loop is running
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.execute_goal_async(goal, ctx=ctx))
            except RuntimeError:
                pass
        return goal

    def cancel_goal(self, goal_id: Optional[str] = None) -> Optional[Goal]:
        """Cancel an in-progress or paused goal."""
        goal = self._resolve_goal(goal_id)
        if not goal:
            return None
        goal.status = GoalStatus.CANCELLED
        self._store.save_goal(goal)
        self._log(f"GOAL: Cancelled '{goal.title}'")
        self._update_ui_progress(goal)
        return goal

    def get_active_goal(self) -> Optional[Goal]:
        if self._active_goal:
            return self._active_goal
        incomplete = self._store.get_incomplete_goals()
        return incomplete[0] if incomplete else None

    def list_goals(self) -> List[Goal]:
        return self._store.load_all_goals()

    def recover_incomplete_goals(self) -> List[Goal]:
        """Check for interrupted goals after restart; puts them in safe PAUSED state."""
        incomplete = self._store.get_incomplete_goals()
        recovered = []
        for g in incomplete:
            if g.status == GoalStatus.RUNNING:
                g.status = GoalStatus.PAUSED
                self._store.save_goal(g)
            recovered.append(g)
            self._log(f"GOAL: Recovered incomplete goal '{g.title}' [{g.id}] in state {g.status.value}")
        return recovered

    def render_goal_markdown(self, goal: Goal) -> str:
        """Format goal progress as a clean markdown dashboard for the UI panel."""
        status_icons = {
            GoalStatus.PENDING: "⏳",
            GoalStatus.PLANNING: "🧠",
            GoalStatus.RUNNING: "⚡",
            GoalStatus.PAUSED: "⏸️",
            GoalStatus.WAITING_FOR_USER: "⚠️",
            GoalStatus.COMPLETED: "✅",
            GoalStatus.FAILED: "❌",
            GoalStatus.CANCELLED: "🚫",
        }
        icon = status_icons.get(goal.status, "🎯")
        completed_count = sum(1 for t in goal.tasks if t.status == TaskStatus.COMPLETED)
        total_count = len(goal.tasks)
        pct = int(goal.progress * 100)

        lines = [
            f"## {icon} Goal: {goal.title}",
            f"**Status:** `{goal.status.value.upper()}`  ·  **Progress:** {completed_count}/{total_count} ({pct}%)",
            "",
            "### 📋 Execution Plan",
        ]

        task_icons = {
            TaskStatus.PENDING: "○",
            TaskStatus.RUNNING: "→",
            TaskStatus.COMPLETED: "✓",
            TaskStatus.FAILED: "✗",
            TaskStatus.SKIPPED: "—",
            TaskStatus.BLOCKED: "⊘",
            TaskStatus.WAITING_FOR_CONFIRMATION: "⚠️",
        }

        for t in goal.tasks:
            t_icon = task_icons.get(t.status, "○")
            action_badge = f"`{t.assigned_action}`" if t.assigned_action else ""
            line = f"- **{t_icon} {t.title}** {action_badge}"
            if t.status == TaskStatus.RUNNING:
                line += " *(running...)*"
            elif t.status == TaskStatus.WAITING_FOR_CONFIRMATION:
                line += " **[CONFIRMATION REQUIRED]**"
            elif t.status == TaskStatus.FAILED and t.error:
                line += f"\n  - *Error: {t.error[:120]}*"
            lines.append(line)

        if goal.error:
            lines.append(f"\n> ⚠️ **Error:** {goal.error}")

        return "\n".join(lines)

    def _resolve_goal(self, goal_id: Optional[str]) -> Optional[Goal]:
        if goal_id:
            return self._store.load_goal(goal_id)
        return self.get_active_goal()

    def _on_progress_event(self, goal: Goal, task: Optional[Task], event: str) -> None:
        self._store.save_goal(goal)
        self._update_ui_progress(goal)
        if task:
            self._log(f"TASK [{task.id}]: {event} — {task.title}")
        else:
            self._log(f"GOAL [{goal.id}]: {event} — {goal.title}")

    def _update_ui_progress(self, goal: Goal) -> None:
        if self._ui_show_content:
            md = self.render_goal_markdown(goal)
            self._ui_show_content(f"GOAL — {goal.title[:30]}", md)

    def _log(self, message: str) -> None:
        if self._ui_write_log:
            try:
                self._ui_write_log(message)
            except Exception:
                pass
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def _record_completion_memory(self, goal: Goal) -> None:
        """Save a concise episodic entry into long-term memory."""
        if not self._save_memory_fn:
            return
        summary = f"Goal '{goal.title}' completed ({len(goal.tasks)} tasks). Objective: {goal.description[:120]}"
        key = f"goal_{goal.id}"
        try:
            self._save_memory_fn({"projects": {key: {"value": summary, "updated": datetime.now().isoformat()}}})
            self._log(f"MEMORY: Stored completion record for '{goal.title}'")
        except Exception as e:
            print(f"[GoalManager] ⚠️ Failed to record memory: {e}")
