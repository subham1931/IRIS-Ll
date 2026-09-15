"""
actions/goal_action.py — Goal & Task Engine tool declaration for IRIS.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from goal.manager import GoalManager
from goal.models import GoalStatus
from goal.persistence import GoalStore

_goal_manager: Optional[GoalManager] = None


def set_goal_manager(manager: GoalManager) -> None:
    """Wire the active GoalManager instance."""
    global _goal_manager
    _goal_manager = manager


def get_goal_manager() -> Optional[GoalManager]:
    return _goal_manager


def manage_goal(parameters: dict, player=None, speak=None, **kwargs) -> str:
    """Handler for manage_goal tool."""
    action = (parameters.get("action") or "status").lower().strip()
    title = parameters.get("title", "").strip()
    description = parameters.get("description", "").strip() or title
    goal_id = parameters.get("goal_id", "").strip()

    mgr = _goal_manager
    if mgr is None:
        # Fallback to local store read if manager not yet wired
        store = GoalStore()
        if action == "list":
            goals = store.load_all_goals()
            if not goals:
                return "No goals found."
            return "\n".join(f"- [{g.status.value.upper()}] {g.title} ({g.id})" for g in goals)
        return "Goal engine is initializing. Please try again in a moment."

    if action == "create":
        if not title and not description:
            return "Please provide a title or description for the goal."
        goal = mgr.create_goal(title=title or description[:50], description=description or title)
        mgr.plan_goal(goal)

        # Start execution in background
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(mgr.execute_goal_async(goal))
        except RuntimeError:
            pass

        return (
            f"Goal '{goal.title}' created with {len(goal.tasks)} tasks. "
            f"Execution started in background. Say 'goal status' to check progress."
        )

    elif action == "pause":
        paused = mgr.pause_goal(goal_id or None)
        if paused:
            return f"Paused goal '{paused.title}' (Progress: {int(paused.progress * 100)}%)."
        return "No active goal to pause."

    elif action == "resume":
        resumed = mgr.resume_goal(goal_id or None)
        if resumed:
            return f"Resumed goal '{resumed.title}'."
        return "No paused goal found to resume."

    elif action == "cancel":
        cancelled = mgr.cancel_goal(goal_id or None)
        if cancelled:
            return f"Cancelled goal '{cancelled.title}'."
        return "No goal found to cancel."

    elif action == "list":
        goals = mgr.list_goals()
        if not goals:
            return "No goals have been created yet."
        lines = ["Here are your recent goals:"]
        for g in goals[-5:]:
            lines.append(f"- **{g.title}** (`{g.status.value.upper()}`, {int(g.progress * 100)}% complete)")
        return "\n".join(lines)

    elif action == "status":
        goal = mgr.get_active_goal()
        if not goal:
            return "There is no active goal running right now."
        pct = int(goal.progress * 100)
        completed = sum(1 for t in goal.tasks if t.status == GoalStatus.COMPLETED or t.status == "completed")
        total = len(goal.tasks)
        curr = goal.get_task(goal.current_task_id) if goal.current_task_id else None
        curr_str = f"Current step: {curr.title}" if curr else "All steps finished or pending."
        return f"Goal '{goal.title}' is {goal.status.value.upper()} ({completed}/{total} tasks, {pct}%). {curr_str}"

    return f"Unknown goal action: '{action}'. Available: create, status, pause, resume, cancel, list."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "manage_goal",
    "description": (
        "Manage multi-step goals and complex task execution. Use this whenever the user wants to "
        "create a multi-step objective, check goal progress, or pause/resume/cancel an ongoing workflow."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action to perform: 'create', 'status', 'pause', 'resume', 'cancel', or 'list'.",
            },
            "title": {
                "type": "STRING",
                "description": "Short title of the goal (required for 'create').",
            },
            "description": {
                "type": "STRING",
                "description": "Detailed description of the goal and what needs to be accomplished.",
            },
            "goal_id": {
                "type": "STRING",
                "description": "Optional specific goal ID to pause, resume, or cancel.",
            },
        },
        "required": ["action"],
    },
    "handler": manage_goal,
}
