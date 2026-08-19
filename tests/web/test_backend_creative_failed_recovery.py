from __future__ import annotations

import json
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
    write_json,
    write_project,
)
from tests.web.test_backend_phase_3a2_creative_generate import creative_brief
from tests.web.web_response_assertions import assert_public_payload


def failed_creative_project(*, project_id: str = "project-a") -> dict:
    data = base_project(project_id=project_id)
    data["status"] = "FAILED"
    data["current_stage"] = "CREATIVE"
    data["stages"]["CREATIVE"]["status"] = "FAILED"
    data["stages"]["CREATIVE"]["attempts"] = 1
    data["last_error"] = {
        "stage": "CREATIVE",
        "type": "PromptGenerationError",
        "message": "mock failure",
        "timestamp": "2026-08-19T03:43:30Z",
    }
    return data


class CreativeFailedRecoveryTests(unittest.TestCase):
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
            failed_creative_project(),
        )
        write_project(
            self.projects_root,
            "project-new",
            base_project(project_id="project-new"),
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
            self.lock_manager,
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)
        self.task_counter = 1

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/creative/retry",
            headers={"X-Correlation-ID": "req_creative_retry"},
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

    def task_id(self) -> str:
        value = f"task_{self.task_counter:032x}"
        self.task_counter += 1
        return value

    def create_task(self, *, operation, status, project_id: str = "project-a"):
        from web_backend.models.tasks import TaskError, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        terminal = status in {
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.CANCELLED,
            TaskStatus.SUCCEEDED,
        }
        record = TaskRecord(
            task_id=self.task_id(),
            project_id=project_id,
            operation=operation,
            status=status,
            created_at=now,
            started_at=None if status is TaskStatus.QUEUED else now,
            finished_at=now if terminal else None,
            correlation_id=f"req_history_{self.task_counter}",
            error=(
                TaskError(
                    code=(
                        "TASK_INTERRUPTED"
                        if status is TaskStatus.INTERRUPTED
                        else "CREATIVE_OUTPUT_INVALID"
                    ),
                    message=(
                        "任务已中断。"
                        if status is TaskStatus.INTERRUPTED
                        else "AI返回的创意内容未通过校验。"
                    ),
                    retryable=status is TaskStatus.FAILED,
                )
                if status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}
                else None
            ),
        )
        return self.application.state.task_repository.create(record)

    def test_01_workflow_exposes_explicit_retry_only_for_failed_initial_state(self):
        failed = self.client.get("/api/projects/project-a/workflow")
        fresh = self.client.get("/api/projects/project-new/workflow")
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["workflow_phase"], "FAILED")
        self.assertEqual(
            failed.json()["available_actions"],
            ["RETRY_GENERATE_CREATIVE"],
        )
        self.assertEqual(fresh.json()["available_actions"], ["GENERATE_CREATIVE"])

    def test_02_retry_endpoint_returns_202_location_and_explicit_operation(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return creative_brief()

        with patch("creative_workflow.generate_creative_brief", side_effect=blocked):
            response = self.post()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"], "CREATIVE_RETRY")
            self.assertEqual(response.json()["status"], "QUEUED")
            self.assertEqual(
                response.headers["Location"],
                f"/api/tasks/{response.json()['task_id']}",
            )
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            self.wait_terminal(response.json()["task_id"])

    def test_03_mock_success_recovers_project_and_stops_at_human_review(self):
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=creative_brief(),
        ) as provider:
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertTrue((self.project_a / "concepts" / "creative_brief.json").is_file())
        workflow = self.client.get("/api/projects/project-a/workflow").json()
        self.assertEqual(workflow["workflow_phase"], "CREATIVE_REVIEW")
        self.assertEqual(workflow["status"], "WAITING_REVIEW")
        self.assertEqual(workflow["stages"]["creative"]["status"], "WAITING_REVIEW")
        self.assertEqual(workflow["stages"]["storyboard"]["status"], "NOT_STARTED")
        self.assertEqual(
            workflow["available_actions"],
            ["APPROVE_CREATIVE", "REVISE_CREATIVE", "REGENERATE_CREATIVE"],
        )
        self.assertFalse((self.project_a / "storyboard" / "storyboard.json").exists())

    def test_04_worker_holds_project_lock_and_revalidates(self):
        from web_backend.services.planning_actions import CreativeActionService

        acquired: list[str] = []
        original = self.lock_manager.project_write

        def recording(project_id: str, *, timeout_seconds: float = 0.0):
            acquired.append(project_id)
            return original(project_id, timeout_seconds=timeout_seconds)

        service: CreativeActionService = self.application.state.creative_action_service
        with (
            patch.object(self.lock_manager, "project_write", side_effect=recording),
            patch.object(
                service,
                "_require_retry_allowed",
                wraps=service._require_retry_allowed,
            ) as validator,
            patch(
                "creative_workflow.generate_creative_brief",
                return_value=creative_brief(),
            ),
        ):
            response = self.post()
            self.wait_terminal(response.json()["task_id"])
        self.assertEqual(acquired, ["project-a"])
        self.assertEqual(validator.call_count, 2)

    def test_05_worker_race_fails_without_provider_call(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with (
            patch.object(
                service,
                "_require_retry_allowed",
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

    def test_06_active_project_task_returns_busy_and_hides_retry_action(self):
        from web_backend.models.tasks import TaskOperation, TaskStatus

        self.create_task(operation=TaskOperation.ASSEMBLY, status=TaskStatus.RUNNING)
        workflow = self.client.get("/api/projects/project-a/workflow")
        response = self.post()
        self.assertEqual(workflow.json()["available_actions"], [])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        tasks = self.application.state.task_repository.list_for_project("project-a")
        self.assertEqual(len(tasks), 1)

    def test_07_semantic_exhaustion_is_output_invalid_and_remains_retryable(self):
        from prompt_generator import StructuredOutputExhaustedError

        provider = Mock(
            side_effect=StructuredOutputExhaustedError("mock structured failure")
        )
        with patch("creative_workflow.generate_creative_brief", provider):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "CREATIVE_OUTPUT_INVALID")
        self.assertEqual(
            task.error.message,
            "AI返回的创意内容未通过校验，可以重新尝试生成。",
        )
        self.assertTrue(task.error.retryable)
        self.assertFalse((self.project_a / "concepts" / "creative_brief.json").exists())
        workflow = self.client.get("/api/projects/project-a/workflow").json()
        self.assertEqual(workflow["available_actions"], ["RETRY_GENERATE_CREATIVE"])

    def test_08_provider_failures_do_not_degrade_to_output_invalid(self):
        from prompt_generator import PromptGenerationError

        for index, detail in enumerate(
            ("authentication failed", "rate limit", "network timeout"),
            start=1,
        ):
            with self.subTest(detail=detail), patch(
                "creative_workflow.generate_creative_brief",
                side_effect=PromptGenerationError(detail),
            ):
                response = self.post()
                task = self.wait_terminal(response.json()["task_id"])
            self.assertEqual(task.error.code, "PROVIDER_REQUEST_FAILED")
            self.assertNotEqual(task.error.code, "CREATIVE_OUTPUT_INVALID")
            self.assertEqual(len(self.application.state.task_repository.list_for_project("project-a")), index)

    def test_09_retry_rejects_canonical_reviewed_and_downstream_states(self):
        cases: list[tuple[str, dict, tuple[str, str] | None]] = []

        canonical = failed_creative_project(project_id="case-canonical")
        cases.append(("case-canonical", canonical, ("concepts", "creative_brief.json")))

        waiting = base_project(project_id="case-waiting")
        waiting["stages"]["CREATIVE"]["status"] = "COMPLETED"
        waiting["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        waiting["current_stage"] = "CREATIVE_REVIEW"
        waiting["status"] = "WAITING_REVIEW"
        cases.append(("case-waiting", waiting, ("concepts", "creative_brief.json")))

        approved = base_project(project_id="case-approved")
        approved["stages"]["CREATIVE"]["status"] = "COMPLETED"
        approved["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        approved["current_stage"] = "STORYBOARD"
        approved["status"] = "APPROVED"
        cases.append(("case-approved", approved, ("concepts", "creative_brief.json")))

        cases.append(
            (
                "case-storyboard",
                failed_creative_project(project_id="case-storyboard"),
                ("storyboard", "storyboard.json"),
            )
        )
        cases.append(
            (
                "case-prompts",
                failed_creative_project(project_id="case-prompts"),
                ("storyboard", "video_prompts.json"),
            )
        )
        cases.append(
            (
                "case-shots",
                failed_creative_project(project_id="case-shots"),
                ("shots", "shot_01.json"),
            )
        )

        provider = Mock(side_effect=AssertionError("provider must not run"))
        with patch("creative_workflow.generate_creative_brief", provider):
            for project_id, data, artifact in cases:
                with self.subTest(project_id=project_id):
                    directory = write_project(self.projects_root, project_id, data)
                    if artifact:
                        write_json(directory.joinpath(*artifact), {})
                    response = self.post(project_id)
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(
                        response.json()["error"]["code"],
                        "ACTION_NOT_ALLOWED",
                    )
        provider.assert_not_called()

    def test_10_retry_failure_task_is_public_safe_and_contains_no_creative(self):
        from prompt_generator import StructuredOutputExhaustedError

        with patch(
            "creative_workflow.generate_creative_brief",
            side_effect=StructuredOutputExhaustedError("private validation detail"),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        task_path = self.runtime_root / "tasks" / f"{task.task_id}.json"
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        assert_public_payload(self, payload)
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("creative_concept", rendered)
        self.assertNotIn("private validation detail", rendered)
        self.assertNotIn("mock-deepseek-key", rendered)

    def test_11_task_history_keeps_failed_generate_and_succeeded_retry(self):
        from web_backend.models.tasks import TaskOperation, TaskStatus

        old = self.create_task(
            operation=TaskOperation.CREATIVE_GENERATE,
            status=TaskStatus.FAILED,
        )
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=creative_brief(),
        ):
            response = self.post()
            current = self.wait_terminal(response.json()["task_id"])
        history = self.client.get("/api/projects/project-a/tasks").json()["tasks"]
        self.assertEqual({item["task_id"] for item in history}, {old.task_id, current.task_id})
        self.assertEqual(
            {item["operation"] for item in history},
            {"CREATIVE_GENERATE", "CREATIVE_RETRY"},
        )

    def test_12_restart_interrupts_retry_without_replaying_provider(self):
        from web_backend.models.tasks import TaskOperation, TaskStatus

        active = self.create_task(
            operation=TaskOperation.CREATIVE_RETRY,
            status=TaskStatus.RUNNING,
        )
        provider = Mock(side_effect=AssertionError("restart must not replay"))
        with patch("creative_workflow.generate_creative_brief", provider):
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
        provider.assert_not_called()
        self.assertEqual(interrupted[0].task_id, active.task_id)
        self.assertEqual(interrupted[0].status.value, "INTERRUPTED")
        workflow = self.client.get("/api/projects/project-a/workflow").json()
        self.assertEqual(workflow["available_actions"], ["RETRY_GENERATE_CREATIVE"])

    def test_13_capability_failure_creates_no_retry_task(self):
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import CreativeActionService

        unavailable = CapabilityService(environment={}, which=lambda _name: None)
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            unavailable,
            self.lock_manager,
        )
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "CAPABILITY_UNAVAILABLE")
        self.assertFalse(self.runtime_root.exists())

    def test_14_openapi_documents_retry_operation(self):
        operation = self.application.openapi()["paths"][
            "/api/projects/{project_id}/planning/creative/retry"
        ]["post"]["responses"]["202"]["content"]["application/json"]["example"][
            "operation"
        ]
        self.assertEqual(operation, "CREATIVE_RETRY")

    def test_15_automated_retry_never_calls_network_minimax_tts_or_ffmpeg(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider network"),
            ),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch(
                "creative_workflow.generate_creative_brief",
                return_value=creative_brief(),
            ) as provider,
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_16_initial_generate_semantic_exhaustion_uses_output_invalid(self):
        from prompt_generator import StructuredOutputExhaustedError

        with patch(
            "creative_workflow.generate_creative_brief",
            side_effect=StructuredOutputExhaustedError("mock structured failure"),
        ) as provider:
            response = self.client.post(
                "/api/projects/project-new/planning/creative/generate",
                headers={"X-Correlation-ID": "req_initial_invalid"},
            )
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.operation.value, "CREATIVE_GENERATE")
        self.assertEqual(task.error.code, "CREATIVE_OUTPUT_INVALID")
        workflow = self.client.get("/api/projects/project-new/workflow").json()
        self.assertEqual(workflow["available_actions"], ["RETRY_GENERATE_CREATIVE"])


if __name__ == "__main__":
    unittest.main()
