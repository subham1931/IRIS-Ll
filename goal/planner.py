"""
goal/planner.py — Structured Task Planner for IRIS Goal Engine.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from goal.models import ExecutionPlan, Goal, GoalStatus, Task, TaskStatus


class TaskPlanner:
    def __init__(self, api_key_provider: Optional[Callable[[], str]] = None):
        self._api_key_provider = api_key_provider

    def _get_api_key(self) -> str:
        if callable(self._api_key_provider):
            return self._api_key_provider()
        try:
            config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
            if config_path.exists():
                return json.loads(config_path.read_text(encoding="utf-8")).get("gemini_api_key", "")
        except Exception:
            pass
        return ""

    def plan_goal(
        self,
        goal: Goal,
        available_tools: List[Dict[str, Any]],
        memory_context: Optional[str] = None,
        conversation_context: Optional[str] = None,
    ) -> ExecutionPlan:
        """Generate a machine-executable DAG plan for the specified goal."""
        goal.status = GoalStatus.PLANNING

        tool_summaries = []
        for t in available_tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            params = t.get("parameters", {})
            tool_summaries.append(f"- {name}: {desc}\n  Parameters schema: {json.dumps(params)}")

        tools_str = "\n".join(tool_summaries)
        api_key = self._get_api_key()

        plan_dict = None
        if api_key:
            try:
                plan_dict = self._llm_plan(
                    goal=goal,
                    tools_str=tools_str,
                    memory_context=memory_context or "",
                    conversation_context=conversation_context or "",
                    api_key=api_key,
                )
            except Exception as e:
                print(f"[Planner] ⚠️ LLM planning failed: {e}, falling back to heuristic planner")

        if not plan_dict:
            plan_dict = self._heuristic_plan(goal, available_tools)

        # Build Task models and ExecutionPlan from plan_dict
        tasks: List[Task] = []
        ordered_task_ids: List[str] = []
        dependencies: Dict[str, List[str]] = {}

        raw_tasks = plan_dict.get("tasks", [])
        for idx, t_data in enumerate(raw_tasks, start=1):
            tid = t_data.get("id") or f"task_{idx}"
            deps = t_data.get("dependencies", [])
            task = Task(
                id=tid,
                goal_id=goal.id,
                title=t_data.get("title", f"Task {idx}"),
                description=t_data.get("description", ""),
                status=TaskStatus.PENDING,
                dependencies=deps,
                assigned_action=t_data.get("assigned_action", ""),
                parameters=t_data.get("parameters", {}),
                max_retries=int(t_data.get("max_retries", 3)),
            )
            tasks.append(task)
            ordered_task_ids.append(tid)
            dependencies[tid] = deps

        plan = ExecutionPlan(
            goal_id=goal.id,
            ordered_task_ids=ordered_task_ids,
            dependencies=dependencies,
            current_task_id=ordered_task_ids[0] if ordered_task_ids else None,
            execution_state="planned",
        )

        goal.tasks = tasks
        goal.plan = plan
        goal.current_task_id = plan.current_task_id
        goal.status = GoalStatus.PENDING
        goal.calculate_progress()
        return plan

    def _llm_plan(
        self,
        goal: Goal,
        tools_str: str,
        memory_context: str,
        conversation_context: str,
        api_key: str,
    ) -> dict:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = f"""You are the Master Task Planner for IRIS AI Assistant.
Create a structured, deterministic, step-by-step machine-executable task plan to achieve this goal.

Goal Title: {goal.title}
Goal Description: {goal.description}

Relevant User/Project Memory:
{memory_context or "None"}

Conversation Context:
{conversation_context or "None"}

Available Actions & Tools:
{tools_str}

CRITICAL RULES:
1. Each task MUST use one of the available tool names in `assigned_action` with valid parameters conforming to its schema.
2. If a task needs the output of a previous task, use string templating in its parameters like:
   "{{{{task_1.output}}}}" or "{{{{task_1.result}}}}".
3. Order tasks in strict execution dependency order.
4. Keep the plan minimal, robust, and complete. Avoid redundant steps.
5. List dependencies as an array of predecessor task IDs (e.g. ["task_1"]).

Return ONLY valid JSON matching this schema — no markdown, no commentary:
{{
  "goal_title": "{goal.title}",
  "tasks": [
    {{
      "id": "task_1",
      "title": "Search for latest React 19 documentation",
      "description": "Perform web search to retrieve relevant updates",
      "assigned_action": "web_search",
      "parameters": {{
        "mode": "research",
        "query": "React 19 release features and changes"
      }},
      "dependencies": [],
      "max_retries": 3
    }},
    {{
      "id": "task_2",
      "title": "Summarize and save notes",
      "description": "Write summary notes to file",
      "assigned_action": "file_controller",
      "parameters": {{
        "action": "write",
        "path": "react_19_summary.md",
        "content": "{{{{task_1.output}}}}"
      }},
      "dependencies": ["task_1"],
      "max_retries": 2
    }}
  ]
}}

JSON:"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = (response.text or "").strip()
        raw = re.sub(r"^```[a-zA-Z]*\r?\n?", "", raw)
        raw = re.sub(r"\r?\n?```\s*$", "", raw).strip()
        return json.loads(raw)

    def _heuristic_plan(self, goal: Goal, available_tools: List[Dict[str, Any]]) -> dict:
        """Deterministic fallback planner for common goal patterns when LLM is unavailable."""
        desc_lower = goal.description.lower()
        tool_names = {t.get("name") for t in available_tools}

        tasks = []

        # Pattern: Search / research and summarize / save
        if "search" in desc_lower or "research" in desc_lower or "find" in desc_lower:
            query = goal.description
            for prefix in ("search for ", "find ", "research "):
                if prefix in desc_lower:
                    query = goal.description[desc_lower.find(prefix) + len(prefix):].strip()
                    break

            if "web_search" in tool_names:
                tasks.append({
                    "id": "task_1",
                    "title": f"Search: {query[:40]}",
                    "description": "Perform web research",
                    "assigned_action": "web_search",
                    "parameters": {"mode": "research", "query": query},
                    "dependencies": [],
                    "max_retries": 3,
                })

            if "save" in desc_lower or "write" in desc_lower:
                if "file_controller" in tool_names:
                    tasks.append({
                        "id": "task_2",
                        "title": "Save search results to file",
                        "description": "Write collected findings to local file",
                        "assigned_action": "file_controller",
                        "parameters": {
                            "action": "write",
                            "path": "search_summary.md",
                            "content": "{{task_1.output}}",
                        },
                        "dependencies": ["task_1"],
                        "max_retries": 2,
                    })

        # Pattern: Build / Inspect / Dev agent
        elif "build" in desc_lower or "inspect" in desc_lower or "deploy" in desc_lower or "project" in desc_lower:
            if "file_processor" in tool_names:
                tasks.append({
                    "id": "task_1",
                    "title": "Inspect project structure",
                    "description": "Examine project files and configuration",
                    "assigned_action": "file_processor",
                    "parameters": {"action": "summarize", "query": "project overview"},
                    "dependencies": [],
                    "max_retries": 2,
                })
            if "code_helper" in tool_names:
                tasks.append({
                    "id": "task_2",
                    "title": "Analyze and validate code",
                    "description": "Validate code syntax and dependencies",
                    "assigned_action": "code_helper",
                    "parameters": {"task": "review", "code": "{{task_1.output}}"},
                    "dependencies": ["task_1"] if tasks else [],
                    "max_retries": 2,
                })

        # Default minimal fallback
        if not tasks:
            first_tool = available_tools[0].get("name") if available_tools else "system_status"
            tasks.append({
                "id": "task_1",
                "title": f"Execute {goal.title}",
                "description": goal.description,
                "assigned_action": first_tool,
                "parameters": {},
                "dependencies": [],
                "max_retries": 3,
            })

        return {"goal_title": goal.title, "tasks": tasks}
