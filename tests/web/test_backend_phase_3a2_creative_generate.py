from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload


def creative_brief():
    from storyboard import CreativeBrief

    return CreativeBrief.model_validate(
        {
            "creative_concept": "清爽柠檬从晨光中出现",
            "target_audience": "年轻消费者",
            "key_message": "自然清爽",
            "visual_direction": "高明度黄色品牌视觉",
            "narrative_arc": "产品亮相到品牌收束",
            "narration_plan": {
                "enabled": False,
                "tone": "",
                "full_script": "",
                "target_duration_seconds": 0,
            },
            "subtitle_strategy": {
                "enabled": False,
                "tone": "",
                "density": "low",
                "max_lines": 1,
                "preferred_position": "none",
                "principles": [],
            },
            "global_constraints": {
                "must": ["自然风格"],
                "must_not": [],
            },
            "av_timeline_constraints": {"forbidden_windows": []},
        }
    )


class WebBackendPhase3A2CreativeGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import CreativeActionService
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.project_a = write_project(
            self.projects_root,
            "project-a",
            base_project(project_id="project-a"),
        )
        self.project_b = write_project(
            self.projects_root,
            "project-b",
            base_project(project_id="project-b", project_name="第二项目"),
        )
        self.lock_manager = ProjectLockManager()
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=2,
            ),
            lock_manager=self.lock_manager,
        )
        self.capabilities = CapabilityService(
            environment={"DEEPSEEK_API_KEY": "mock-deepseek-key"},
            which=lambda _name: None,
        )
        self.application.state.capability_service = self.capabilities
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            self.capabilities,
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/creative/generate",
            headers={"X-Correlation-ID": "req_phase3a2"},
        )

    def wait_terminal(self, task_id: str, timeout: float = 3.0):
        from web_backend.models.tasks import TERMINAL_TASK_STATUSES

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.application.state.task_repository.get(task_id)
            if record.status in TERMINAL_TASK_STATUSES:
                return record
            Event().wait(0.01)
        self.fail(f"task {task_id} did not become terminal")

    def successful_post(self, project_id: str = "project-a"):
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=creative_brief(),
        ) as provider:
            response = self.post(project_id)
            terminal = self.wait_terminal(response.json()["task_id"])
        return response, terminal, provider

    def mark_creative_waiting_review(self, project_dir: Path) -> None:
        project_file = project_dir / "project.json"
        data = json.loads(project_file.read_text(encoding="utf-8"))
        data["stages"]["CREATIVE"]["status"] = "COMPLETED"
        data["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        data["current_stage"] = "CREATIVE_REVIEW"
        data["status"] = "WAITING_REVIEW"
        write_json(project_file, data)

    def test_01_valid_generate_returns_202_queued_and_location(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=blocked):
            response = self.post()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "QUEUED")
            self.assertEqual(response.json()["operation"], "CREATIVE_GENERATE")
            self.assertEqual(
                response.headers["Location"],
                f"/api/tasks/{response.json()['task_id']}",
            )
            self.assertTrue(entered.wait(timeout=1))
            running = self.application.state.task_repository.get(
                response.json()["task_id"]
            )
            self.assertEqual(running.status.value, "RUNNING")
            release.set()
            self.wait_terminal(running.task_id)

    def test_02_mock_core_success_persists_creative_and_real_review_state(self):
        response, task, provider = self.successful_post()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.status.value, "SUCCEEDED")
        provider.assert_called_once()
        saved = json.loads(
            (self.project_a / "concepts" / "creative_brief.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["creative_concept"], "清爽柠檬从晨光中出现")
        project = json.loads((self.project_a / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(project["stages"]["CREATIVE"]["status"], "COMPLETED")
        self.assertEqual(
            project["stages"]["CREATIVE_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        self.assertEqual(project["current_stage"], "CREATIVE_REVIEW")

    def test_03_task_result_is_a_small_reference_not_creative_content(self):
        _response, task, _provider = self.successful_post()
        self.assertEqual(task.result.resource_type, "CREATIVE")
        self.assertEqual(task.result.resource_id, "project-a")
        serialized = task.model_dump_json()
        self.assertNotIn("清爽柠檬从晨光中出现", serialized)
        self.assertNotIn("creative_concept", serialized)

    def test_04_existing_creative_is_action_not_allowed_without_provider_or_task(self):
        self.mark_creative_waiting_review(self.project_a)
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with patch("creative_workflow.generate_creative_brief", provider):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        provider.assert_not_called()
        self.assertFalse(self.runtime_root.exists())

    def test_05_capability_unavailable_does_not_create_task(self):
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import CreativeActionService

        unavailable = CapabilityService(environment={}, which=lambda _name: None)
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            unavailable,
        )
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "CAPABILITY_UNAVAILABLE")
        self.assertFalse(self.runtime_root.exists())

    def test_06_same_project_active_task_returns_project_busy_without_second_task(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=blocked):
            first = self.post()
            self.assertTrue(entered.wait(timeout=1))
            second = self.post()
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["error"]["code"], "PROJECT_BUSY")
            tasks = self.application.state.task_repository.list_for_project("project-a")
            self.assertEqual(len(tasks), 1)
            release.set()
            self.wait_terminal(first.json()["task_id"])

    def test_07_different_projects_can_execute_independently(self):
        release = Event()

        def blocked(_request, *_args, **_kwargs):
            release.wait(timeout=5)
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=blocked):
            first = self.post("project-a")
            second = self.post("project-b")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(
                len(self.application.state.task_repository.list_for_project("project-a")),
                1,
            )
            self.assertEqual(
                len(self.application.state.task_repository.list_for_project("project-b")),
                1,
            )
            release.set()
            self.assertEqual(self.wait_terminal(first.json()["task_id"]).status.value, "SUCCEEDED")
            self.assertEqual(self.wait_terminal(second.json()["task_id"]).status.value, "SUCCEEDED")

    def test_08_worker_runs_under_project_write_lock(self):
        acquired: list[str] = []
        original = self.lock_manager.project_write

        def recording(project_id: str, *, timeout_seconds: float = 0.0):
            acquired.append(project_id)
            return original(project_id, timeout_seconds=timeout_seconds)

        with (
            patch.object(self.lock_manager, "project_write", side_effect=recording),
            patch("creative_workflow.generate_creative_brief", return_value=creative_brief()),
        ):
            response = self.post()
            self.wait_terminal(response.json()["task_id"])
        self.assertEqual(acquired, ["project-a"])

    def test_09_worker_performs_second_state_validation_and_race_skips_provider(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with (
            patch.object(
                service,
                "_require_generate_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("creative_workflow.generate_creative_brief", provider),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(validator.call_count, 2)
        provider.assert_not_called()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "ACTION_NOT_ALLOWED")

    def test_10_provider_failure_is_safe_and_web_does_not_retry(self):
        from prompt_generator import PromptGenerationError

        secret = r"D:\private DEEPSEEK_API_KEY=credential raw API response"
        provider = Mock(side_effect=PromptGenerationError(secret))
        with patch("creative_workflow.generate_creative_brief", provider):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "PROVIDER_REQUEST_FAILED")
        rendered = (
            self.runtime_root / "tasks" / f"{task.task_id}.json"
        ).read_text(encoding="utf-8")
        for forbidden in ("D:\\private", "DEEPSEEK_API_KEY", "credential", "raw API"):
            self.assertNotIn(forbidden, rendered)

    def test_11_task_get_and_project_tasks_expose_generated_task(self):
        response, task, _provider = self.successful_post()
        task_get = self.client.get(f"/api/tasks/{task.task_id}")
        project_tasks = self.client.get("/api/projects/project-a/tasks")
        self.assertEqual(task_get.status_code, 200)
        self.assertEqual(task_get.json()["status"], "SUCCEEDED")
        self.assertEqual(project_tasks.status_code, 200)
        self.assertEqual(project_tasks.json()["tasks"][0]["task_id"], task.task_id)
        self.assertEqual(response.json()["task_id"], task.task_id)

    def test_12_active_creative_task_is_recoverable_from_project_tasks(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=blocked):
            response = self.post()
            self.assertTrue(entered.wait(timeout=1))
            tasks = self.client.get("/api/projects/project-a/tasks").json()["tasks"]
            self.assertEqual(tasks[0]["operation"], "CREATIVE_GENERATE")
            self.assertEqual(tasks[0]["status"], "RUNNING")
            release.set()
            self.wait_terminal(response.json()["task_id"])

    def test_13_restart_interrupts_running_without_replay(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus
        from web_backend.repositories.task_repository import TaskRepository

        repository = TaskRepository(self.runtime_root)
        record = TaskRecord(
            task_id="task_" + "a" * 32,
            project_id="project-a",
            operation=TaskOperation.CREATIVE_GENERATE,
            status=TaskStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            correlation_id="req_restart",
        )
        repository.create(record)
        provider = Mock(side_effect=AssertionError("restart must not replay"))
        with patch("creative_workflow.generate_creative_brief", provider):
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
        self.assertEqual(interrupted[0].status.value, "INTERRUPTED")
        provider.assert_not_called()

    def test_14_invalid_and_missing_projects_are_safe(self):
        invalid = self.post("C:secret")
        missing = self.post("missing-project")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "INVALID_PROJECT_ID")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "PROJECT_NOT_FOUND")
        assert_public_payload(self, invalid.json())
        assert_public_payload(self, missing.json())

    def test_15_response_and_task_contain_no_absolute_path(self):
        response, task, _provider = self.successful_post()
        assert_public_payload(self, response.json())
        assert_public_payload(self, task.model_dump(mode="json"))

    def test_16_action_uses_core_request_including_user_notes_and_constraints(self):
        captured = {}

        def fake(request, *_args, **_kwargs):
            captured.update(request.model_dump())
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=fake):
            response = self.post()
            self.wait_terminal(response.json()["task_id"])
        self.assertEqual(captured["product_name"], "柠檬饮料")
        self.assertEqual(captured["user_notes"], "突出自然风格")
        self.assertEqual(captured["duration_seconds"], 18)

    def test_17_automated_action_never_calls_real_network_minimax_tts_or_ffmpeg(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("provider")),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch("creative_workflow.generate_creative_brief", return_value=creative_brief()),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_18_get_paths_remain_zero_write_before_any_submit(self):
        before_projects = tree_snapshot(self.projects_root)
        self.assertFalse(self.runtime_root.exists())
        self.assertEqual(self.client.get("/api/projects/project-a").status_code, 200)
        self.assertEqual(self.client.get("/api/projects/project-a/workflow").status_code, 200)
        self.assertEqual(
            self.client.get("/api/projects/project-a/planning/creative").status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/projects/project-a/tasks").status_code, 200)
        self.assertEqual(tree_snapshot(self.projects_root), before_projects)
        self.assertFalse(self.runtime_root.exists())

    def test_19_no_public_generic_task_submit_endpoint_exists(self):
        response = self.client.post(
            "/api/tasks",
            json={"operation": "CREATIVE_GENERATE", "project_id": "project-a"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "ROUTE_NOT_FOUND")

    def test_20_capability_response_still_exposes_boolean_only(self):
        response = self.client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        assert_public_payload(self, response.json())
        self.assertEqual(response.json()["planning"]["deepseek"], {"available": True})
        self.assertNotIn("mock-deepseek-key", json.dumps(response.json()))


if __name__ == "__main__":
    unittest.main()
