"""
goal package — IRIS Goal and Task Engine.
"""
from goal.models import (
    Goal,
    GoalStatus,
    Task,
    TaskStatus,
    TaskResult,
    ExecutionPlan,
    ErrorClassification,
)
from goal.detector import GoalDetector, GoalDetectionResult
from goal.planner import TaskPlanner
from goal.executor import TaskExecutor
from goal.verifier import TaskVerifier, VerificationResult
from goal.persistence import GoalStore
from goal.manager import GoalManager

__all__ = [
    "Goal",
    "GoalStatus",
    "Task",
    "TaskStatus",
    "TaskResult",
    "ExecutionPlan",
    "ErrorClassification",
    "GoalDetector",
    "GoalDetectionResult",
    "TaskPlanner",
    "TaskExecutor",
    "TaskVerifier",
    "VerificationResult",
    "GoalStore",
    "GoalManager",
]
