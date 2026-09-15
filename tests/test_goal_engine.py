"""
tests/test_goal_engine.py — Unit and integration tests for IRIS Goal & Task Engine.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from goal.detector import GoalDetector
from goal.executor import TaskExecutor
from goal.manager import GoalManager
from goal.models import (
    ErrorClassification,
    ExecutionPlan,
    Goal,
    GoalStatus,
    Task,
    TaskResult,
    TaskStatus,
)
from goal.persistence import GoalStore
from goal.planner import TaskPlanner
from goal.verifier import TaskVerifier, VerificationResult


class TestGoalModels(unittest.TestCase):
    def test_goal_serialization(self):
        goal = Goal(
            id="goal_test1",
            title="Deploy Application",
            description="Build, test, and deploy project",
            status=GoalStatus.PENDING,
            priority="high",
        )
        task1 = Task(
            id="task_1",
            goal_id="goal_test1",
            title="Inspect project",
            assigned_action="file_processor",
            parameters={"query": "summary"},
        )
        task2 = Task(
            id="task_2",
            goal_id="goal_test1",
            title="Run build",
            assigned_action="code_helper",
            dependencies=["task_1"],
        )
        goal.tasks = [task1, task2]
        goal.plan = ExecutionPlan(
            goal_id="goal_test1",
            ordered_task_ids=["task_1", "task_2"],
            dependencies={"task_2": ["task_1"]},
        )

        d = goal.to_dict()
        self.assertEqual(d["id"], "goal_test1")
        self.assertEqual(len(d["tasks"]), 2)

        restored = Goal.from_dict(d)
        self.assertEqual(restored.id, "goal_test1")
        self.assertEqual(restored.title, "Deploy Application")
        self.assertEqual(len(restored.tasks), 2)
        self.assertEqual(restored.tasks[1].dependencies, ["task_1"])
        self.assertEqual(restored.calculate_progress(), 0.0)

        restored.tasks[0].status = TaskStatus.COMPLETED
        self.assertEqual(restored.calculate_progress(), 0.5)


class TestGoalDetector(unittest.TestCase):
    def setUp(self):
        self.detector = GoalDetector(api_key_provider=lambda: "")

    def test_simple_command_detection(self):
        simple_queries = [
            "Open Chrome",
            "Mute",
            "Volume up",
            "Turn on wifi",
            "What time is it",
            "What's the weather in Tokyo",
            "Take a screenshot",
            "Shutdown",
        ]
        for query in simple_queries:
            res = self.detector.detect(query)
            self.assertFalse(res.is_goal, f"Expected '{query}' to be classified as simple command")

    def test_multi_step_goal_detection(self):
        multi_queries = [
            "Search for latest Next.js 15 changes and summarize them and save to notes",
            "Inspect this project and then fix any build errors",
            "Prepare project for deployment",
            "Research React state management and compare features and write report",
            "Audit dependencies and update packages step by step",
        ]
        for query in multi_queries:
            res = self.detector.detect(query)
            self.assertTrue(res.is_goal, f"Expected '{query}' to be classified as multi-step goal")
            self.assertTrue(res.requires_planning)
            self.assertTrue(len(res.goal_title) > 0)


class TestTaskPlanner(unittest.TestCase):
    def setUp(self):
        self.planner = TaskPlanner(api_key_provider=lambda: "")
        self.tools = [
            {
                "name": "web_search",
                "description": "Searches the web",
                "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}},
            },
            {
                "name": "file_controller",
                "description": "Read/write files",
                "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING"}, "path": {"type": "STRING"}}},
            },
            {
                "name": "file_processor",
                "description": "Inspect and summarize files",
                "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}},
            },
        ]

    def test_heuristic_planning(self):
        goal = Goal(
            id="g_100",
            title="Search React updates and save",
            description="Search for React 19 documentation and then save summary to file",
        )
        plan = self.planner.plan_goal(goal, self.tools)
        self.assertIsNotNone(plan)
        self.assertTrue(len(goal.tasks) >= 2)
        self.assertEqual(goal.tasks[0].assigned_action, "web_search")
        self.assertEqual(goal.tasks[1].assigned_action, "file_controller")
        self.assertIn("task_1", goal.tasks[1].dependencies)


class TestTaskVerifier(unittest.TestCase):
    def setUp(self):
        self.verifier = TaskVerifier()
        self.task = Task(id="t1", goal_id="g1", title="Test Task", assigned_action="web_search")

    def test_verify_success(self):
        res = self.verifier.verify(self.task, "Found 10 articles on React 19")
        self.assertTrue(res.success)
        self.assertFalse(res.needs_retry)

    def test_verify_recoverable_error(self):
        res = self.verifier.verify(self.task, "Connection timed out after 10000ms")
        self.assertFalse(res.success)
        self.assertTrue(res.needs_retry)
        self.assertEqual(res.error_type, ErrorClassification.RECOVERABLE)

    def test_verify_confirmation_gate(self):
        destructive_task = Task(id="t_del", goal_id="g1", title="Delete file", assigned_action="delete_file")
        res = self.verifier.verify(destructive_task, "File ready for deletion")
        self.assertFalse(res.success)
        self.assertTrue(res.requires_user_confirmation)
        self.assertEqual(res.error_type, ErrorClassification.USER_INTERVENTION)


class TestTaskExecutor(unittest.TestCase):
    def test_dag_execution_and_templating(self):
        mock_tool_store = {}

        def mock_runner(action, params, ctx):
            if action == "web_search":
                return f"Research results for {params.get('query')}"
            elif action == "file_controller":
                path = params.get("path")
                content = params.get("content")
                mock_tool_store[path] = content
                return f"Wrote {len(content)} chars to {path}"
            return "ok"

        executor = TaskExecutor(action_runner=mock_runner)

        goal = Goal(id="g_exec", title="Research and Save", description="Run pipeline")
        task1 = Task(
            id="task_1",
            goal_id="g_exec",
            title="Search",
            assigned_action="web_search",
            parameters={"query": "AI Trends"},
        )
        task2 = Task(
            id="task_2",
            goal_id="g_exec",
            title="Save",
            assigned_action="file_controller",
            parameters={"path": "trends.txt", "content": "{{task_1.output}}"},
            dependencies=["task_1"],
        )
        goal.tasks = [task1, task2]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            executed_goal = loop.run_until_complete(executor.run_goal_async(goal))
            self.assertEqual(executed_goal.status, GoalStatus.COMPLETED)
            self.assertEqual(task1.status, TaskStatus.COMPLETED)
            self.assertEqual(task2.status, TaskStatus.COMPLETED)
            self.assertIn("trends.txt", mock_tool_store)
            self.assertEqual(mock_tool_store["trends.txt"], "Research results for AI Trends")
        finally:
            loop.close()

    def test_retry_on_recoverable_failure(self):
        call_count = 0

        def failing_runner(action, params, ctx):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return "Error: Connection timed out"
            return "Success on 3rd attempt"

        executor = TaskExecutor(action_runner=failing_runner)
        goal = Goal(id="g_retry", title="Retry Goal", description="Test retry")
        task = Task(id="t_r", goal_id="g_retry", title="Network Task", assigned_action="web_search", max_retries=3)
        goal.tasks = [task]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            executed_goal = loop.run_until_complete(executor.run_goal_async(goal))
            self.assertEqual(executed_goal.status, GoalStatus.COMPLETED)
            self.assertEqual(task.status, TaskStatus.COMPLETED)
            self.assertEqual(task.retry_count, 2)
            self.assertEqual(task.result, "Success on 3rd attempt")
        finally:
            loop.close()


class TestGoalPersistenceAndManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp_dir.name) / "test_goals.json"
        self.store = GoalStore(storage_path=self.store_path)

        def mock_runner(action, params, ctx):
            return f"Executed {action} with {params}"

        self.tools = [
            {"name": "web_search", "description": "Search", "parameters": {"type": "OBJECT", "properties": {}}},
            {"name": "file_controller", "description": "File", "parameters": {"type": "OBJECT", "properties": {}}},
        ]
        self.memory_records = {}

        def mock_save_memory(data):
            self.memory_records.update(data)

        self.manager = GoalManager(
            action_runner=mock_runner,
            get_tools_fn=lambda: self.tools,
            get_memory_fn=lambda: "Test Memory Context",
            save_memory_fn=mock_save_memory,
            api_key_provider=lambda: "",
            store=self.store,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_lifecycle_pause_resume_cancel(self):
        goal = self.manager.create_goal(title="Test Pipeline", description="Multi step")
        self.manager.plan_goal(goal)
        self.assertEqual(goal.status, GoalStatus.PENDING)

        paused = self.manager.pause_goal(goal.id)
        self.assertEqual(paused.status, GoalStatus.PAUSED)

        resumed = self.manager.resume_goal(goal.id)
        self.assertEqual(resumed.status, GoalStatus.RUNNING)

        cancelled = self.manager.cancel_goal(goal.id)
        self.assertEqual(cancelled.status, GoalStatus.CANCELLED)

    def test_restart_recovery(self):
        goal = self.manager.create_goal(title="Crashed Job", description="Was running before crash")
        goal.status = GoalStatus.RUNNING
        self.store.save_goal(goal)

        # Simulate restart
        new_manager = GoalManager(
            action_runner=lambda a, p, c: "ok",
            get_tools_fn=lambda: self.tools,
            api_key_provider=lambda: "",
            store=self.store,
        )
        recovered = new_manager.recover_incomplete_goals()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].id, goal.id)
        self.assertEqual(recovered[0].status, GoalStatus.PAUSED)

    def test_full_goal_flow_and_episodic_memory(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            executed = loop.run_until_complete(
                self.manager.handle_request("Search for AI tools and save summary")
            )
            self.assertIsNotNone(executed)
            self.assertEqual(executed.status, GoalStatus.COMPLETED)
            self.assertEqual(len(executed.tasks), 2)
            self.assertIn("projects", self.memory_records)
            self.assertIn(f"goal_{executed.id}", self.memory_records["projects"])
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
