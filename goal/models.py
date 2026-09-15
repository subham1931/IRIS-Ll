"""
goal/models.py — Data models for IRIS Goal & Task Engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class GoalStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"


class ErrorClassification(str, Enum):
    RECOVERABLE = "recoverable"
    USER_INTERVENTION = "user_intervention"
    NON_RECOVERABLE = "non_recoverable"


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class TaskResult:
    success: bool
    output: Any = None
    error: Optional[str] = None
    recoverable: bool = False
    requires_confirmation: bool = False
    confirmation_details: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "recoverable": self.recoverable,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_details": self.confirmation_details,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskResult:
        return cls(
            success=data.get("success", False),
            output=data.get("output"),
            error=data.get("error"),
            recoverable=data.get("recoverable", False),
            requires_confirmation=data.get("requires_confirmation", False),
            confirmation_details=data.get("confirmation_details"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Task:
    id: str
    goal_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    assigned_action: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: str = field(default_factory=_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, TaskStatus) else str(self.status),
            "dependencies": list(self.dependencies),
            "assigned_action": self.assigned_action,
            "parameters": dict(self.parameters),
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        raw_status = data.get("status", TaskStatus.PENDING.value)
        try:
            status = TaskStatus(raw_status)
        except ValueError:
            status = TaskStatus.PENDING

        return cls(
            id=data["id"],
            goal_id=data.get("goal_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=status,
            dependencies=list(data.get("dependencies", [])),
            assigned_action=data.get("assigned_action", ""),
            parameters=dict(data.get("parameters", {})),
            result=data.get("result"),
            error=data.get("error"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            created_at=data.get("created_at", _now_iso()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class ExecutionPlan:
    goal_id: str
    ordered_task_ids: List[str] = field(default_factory=list)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    current_task_id: Optional[str] = None
    execution_state: str = "initialized"

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "ordered_task_ids": list(self.ordered_task_ids),
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "current_task_id": self.current_task_id,
            "execution_state": self.execution_state,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionPlan:
        return cls(
            goal_id=data.get("goal_id", ""),
            ordered_task_ids=list(data.get("ordered_task_ids", [])),
            dependencies={k: list(v) for k, v in data.get("dependencies", {}).items()},
            current_task_id=data.get("current_task_id"),
            execution_state=data.get("execution_state", "initialized"),
        )


@dataclass
class Goal:
    id: str
    title: str
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    priority: str = "normal"  # "low" | "normal" | "high" | "critical"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    tasks: List[Task] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    current_task_id: Optional[str] = None
    progress: float = 0.0  # 0.0 to 1.0
    plan: Optional[ExecutionPlan] = None
    error: Optional[str] = None

    def calculate_progress(self) -> float:
        if not self.tasks:
            return 0.0
        completed = sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)
        self.progress = round(completed / len(self.tasks), 2)
        return self.progress

    def get_task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, GoalStatus) else str(self.status),
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tasks": [t.to_dict() for t in self.tasks],
            "metadata": dict(self.metadata),
            "current_task_id": self.current_task_id,
            "progress": self.progress,
            "plan": self.plan.to_dict() if self.plan else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Goal:
        raw_status = data.get("status", GoalStatus.PENDING.value)
        try:
            status = GoalStatus(raw_status)
        except ValueError:
            status = GoalStatus.PENDING

        tasks = [Task.from_dict(t) for t in data.get("tasks", [])]
        plan_data = data.get("plan")
        plan = ExecutionPlan.from_dict(plan_data) if plan_data else None

        return cls(
            id=data["id"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=status,
            priority=data.get("priority", "normal"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            tasks=tasks,
            metadata=dict(data.get("metadata", {})),
            current_task_id=data.get("current_task_id"),
            progress=float(data.get("progress", 0.0)),
            plan=plan,
            error=data.get("error"),
        )
