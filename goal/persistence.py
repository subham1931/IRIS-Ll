"""
goal/persistence.py — JSON-based goal state persistence for IRIS.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional

from goal.models import Goal, GoalStatus


class GoalStore:
    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            base_dir = Path(__file__).resolve().parent.parent
            storage_path = base_dir / "memory" / "goals.json"
        self._path = storage_path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_data(self) -> Dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            content = self._path.read_text(encoding="utf-8").strip()
            if not content:
                return {}
            return json.loads(content)
        except Exception as e:
            print(f"[GoalStore] ⚠️ Read error from {self._path.name}: {e}")
            return {}

    def _write_data(self, data: Dict[str, dict]) -> None:
        try:
            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(self._path)
        except Exception as e:
            print(f"[GoalStore] ⚠️ Write error to {self._path.name}: {e}")

    def save_goal(self, goal: Goal) -> None:
        """Save or update a goal record."""
        with self._lock:
            data = self._read_data()
            data[goal.id] = goal.to_dict()
            self._write_data(data)

    def load_goal(self, goal_id: str) -> Optional[Goal]:
        """Load a specific goal by ID."""
        with self._lock:
            data = self._read_data()
            goal_data = data.get(goal_id)
            if goal_data:
                try:
                    return Goal.from_dict(goal_data)
                except Exception as e:
                    print(f"[GoalStore] ⚠️ Failed to parse goal {goal_id}: {e}")
            return None

    def load_all_goals(self) -> List[Goal]:
        """Load all saved goals."""
        with self._lock:
            data = self._read_data()
            goals = []
            for gid, gdata in data.items():
                try:
                    goals.append(Goal.from_dict(gdata))
                except Exception as e:
                    print(f"[GoalStore] ⚠️ Failed to parse goal {gid}: {e}")
            return goals

    def delete_goal(self, goal_id: str) -> bool:
        """Remove a goal record."""
        with self._lock:
            data = self._read_data()
            if goal_id in data:
                del data[goal_id]
                self._write_data(data)
                return True
            return False

    def get_incomplete_goals(self) -> List[Goal]:
        """Return goals that were interrupted or remain unfinished."""
        all_goals = self.load_all_goals()
        incomplete_statuses = {
            GoalStatus.PENDING,
            GoalStatus.PLANNING,
            GoalStatus.RUNNING,
            GoalStatus.PAUSED,
            GoalStatus.WAITING_FOR_USER,
        }
        return [g for g in all_goals if g.status in incomplete_statuses]
