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
    write_json,
    write_project,
)
from tests.web.test_backend_phase_3a2_creative_generate import creative_brief
from tests.web.web_response_assertions import assert_public_payload


FEEDBACK = "保留核心概念，但不要出现人物，增加产品微距和清爽感。"


def revised_brief(label: str = "修改后的清爽微距创意"):
    return creative_brief().model_copy(update={"creative_concept": label})


class WebBackendPhase3A3BCreativeRevisionTests(unittest.TestCase):
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
        self.project_a = self._write_waiting_project("project-a", "第一项目")
        self.project_b = self._write_waiting_project("project-b", "第二项目")
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

    def _write_waiting_project(self, project_id: str, name: str) -> Path:
        project = base_project(project_id=project_id, project_name=name)
        project["stages"]["CREATIVE"]["status"] = "COMPLETED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "CREATIVE_REVIEW"
        project["status"] = "WAITING_REVIEW"
        directory = write_project(self.projects_root, project_id, project)
        write_json(
            directory / "concepts" / "creative_brief.json",
            creative_brief().model_dump(),
        )
        return directory

    def post_revise(self, project_id: str = "project-a", payload=None):
        return self.client.post(
            f"/api/projects/{project_id}/planning/creative/revise",
            json={"feedback": FEEDBACK} if payload is None else payload,
            headers={"X-Correlation-ID": "req_phase3a3b_revise"},
        )

    def post_regenerate(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/creative/regenerate",
            headers={"X-Correlation-ID": "req_phase3a3b_regenerate"},
        )

    def wait_terminal(self, task_id: str, timeout: float = 3.0):
        from web_backend.models.tasks import TERMINAL_TASK_STATUSES

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.application.state.task_repository.get(task_id)
            if task.status in TERMINAL_TASK_STATUSES:
                return task
            Event().wait(0.01)
        self.fail(f"task {task_id} did not become terminal")

    @staticmethod
    def read_project(directory: Path) -> dict:
        return json.loads((directory / "project.json").read_text(encoding="utf-8"))

    def set_review_status(self, status: str) -> None:
        project = self.read_project(self.project_a)
        if status == "NOT_STARTED":
            project["stages"]["CREATIVE"]["status"] = "NOT_STARTED"
            project["current_stage"] = "CREATIVE"
        else:
            project["stages"]["CREATIVE"]["status"] = "COMPLETED"
            project["current_stage"] = "CREATIVE_REVIEW"
        project["stages"]["CREATIVE_REVIEW"]["status"] = status
        project["status"] = status
        write_json(self.project_a / "project.json", project)

    def create_active_task(self, project_id: str = "project-a") -> None:
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + ("a" if project_id == "project-a" else "b") * 32,
                project_id=project_id,
                operation=TaskOperation.CREATIVE_GENERATE,
                status=TaskStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
                correlation_id="req_active",
            )
        )

    def test_01_revise_returns_202_distinct_operation_and_location(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return revised_brief()

        with patch("creative_workflow.revise_creative_brief", side_effect=blocked):
            response = self.post_revise()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"], "CREATIVE_REVISE")
            self.assertEqual(
                response.headers["Location"],
                f"/api/tasks/{response.json()['task_id']}",
            )
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            self.wait_terminal(response.json()["task_id"])

    def test_02_regenerate_returns_202_without_body_and_distinct_operation(self):
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=revised_brief("全新创意"),
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "CREATIVE_REGENERATE")
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_03_revise_passes_trimmed_feedback_current_and_original_request(self):
        captured: dict = {}

        def fake(request, current, feedback, *_args, **_kwargs):
            captured.update(
                request=request.model_dump(),
                current=current.model_dump(),
                feedback=feedback,
            )
            return revised_brief()

        with patch("creative_workflow.revise_creative_brief", side_effect=fake):
            response = self.post_revise(payload={"feedback": f"  {FEEDBACK}  "})
            self.wait_terminal(response.json()["task_id"])
        self.assertEqual(captured["feedback"], FEEDBACK)
        self.assertEqual(captured["current"]["creative_concept"], creative_brief().creative_concept)
        self.assertEqual(captured["request"]["product_name"], "柠檬饮料")
        self.assertEqual(captured["request"]["user_notes"], "突出自然风格")

    def test_04_regenerate_uses_original_request_without_feedback_or_current(self):
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=revised_brief("全新方案"),
        ) as provider:
            response = self.post_regenerate()
            self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args[0].product_name, "柠檬饮料")
        self.assertEqual(provider.call_args.args[0].user_notes, "突出自然风格")
        self.assertNotIn(FEEDBACK, str(provider.call_args))
        self.assertNotIn("current", provider.call_args.kwargs)

    def test_05_success_replaces_canonical_and_resets_only_review_to_waiting(self):
        with patch(
            "creative_workflow.revise_creative_brief",
            return_value=revised_brief(),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        saved = json.loads(
            (self.project_a / "concepts" / "creative_brief.json").read_text(
                encoding="utf-8"
            )
        )
        project = self.read_project(self.project_a)
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(saved["creative_concept"], "修改后的清爽微距创意")
        self.assertEqual(project["stages"]["CREATIVE"]["status"], "COMPLETED")
        self.assertEqual(project["stages"]["CREATIVE_REVIEW"]["status"], "WAITING_REVIEW")
        self.assertEqual(project["stages"]["STORYBOARD"]["status"], "NOT_STARTED")
        self.assertFalse((self.project_a / "storyboard" / "storyboard.json").exists())

    def test_06_regenerate_success_replaces_canonical_and_remains_waiting_review(self):
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=revised_brief("完全不同的新方案"),
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        saved = json.loads(
            (self.project_a / "concepts" / "creative_brief.json").read_text(encoding="utf-8")
        )
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(saved["creative_concept"], "完全不同的新方案")
        self.assertEqual(
            self.read_project(self.project_a)["stages"]["CREATIVE_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_07_feedback_validation_is_bounded_safe_and_forbids_extra_fields(self):
        for payload in (
            {"feedback": ""},
            {"feedback": "   "},
            {"feedback": "x" * 4001},
            {"feedback": FEEDBACK, "api_key": "secret"},
        ):
            with self.subTest(payload=list(payload)):
                response = self.post_revise(payload=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
                self.assertNotIn("secret", response.text)
        self.assertFalse(self.runtime_root.exists())

    def test_08_feedback_is_absent_from_task_dto_and_runtime_json(self):
        with patch(
            "creative_workflow.revise_creative_brief",
            return_value=revised_brief(),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        rendered = (self.runtime_root / "tasks" / f"{task.task_id}.json").read_text(
            encoding="utf-8"
        )
        for payload in (response.text, task.model_dump_json(), rendered):
            self.assertNotIn(FEEDBACK, payload)
            self.assertNotIn("creative_concept", payload)
            self.assertNotIn("mock-deepseek-key", payload)
        self.assertEqual(task.result.resource_type, "CREATIVE")
        self.assertEqual(task.result.resource_id, "project-a")
        self.assertIsNone(task.result.version)

    def test_09_failure_calls_provider_once_and_preserves_old_creative_and_review(self):
        from prompt_generator import PromptGenerationError

        for endpoint, target in (
            (self.post_revise, "creative_workflow.revise_creative_brief"),
            (self.post_regenerate, "creative_workflow.generate_creative_brief"),
        ):
            with self.subTest(endpoint=endpoint.__name__):
                before = (self.project_a / "concepts" / "creative_brief.json").read_bytes()
                provider = Mock(side_effect=PromptGenerationError("private provider error"))
                with patch(target, provider):
                    response = endpoint()
                    task = self.wait_terminal(response.json()["task_id"])
                provider.assert_called_once()
                self.assertEqual(task.status.value, "FAILED")
                self.assertEqual(task.error.code, "PROVIDER_REQUEST_FAILED")
                self.assertEqual(
                    (self.project_a / "concepts" / "creative_brief.json").read_bytes(),
                    before,
                )
                self.assertEqual(
                    self.read_project(self.project_a)["stages"]["CREATIVE_REVIEW"]["status"],
                    "WAITING_REVIEW",
                )

    def test_10_not_started_or_approved_is_action_not_allowed_without_task(self):
        for status in ("NOT_STARTED", "APPROVED"):
            with self.subTest(status=status):
                self.set_review_status(status)
                revise = self.post_revise()
                regenerate = self.post_regenerate()
                self.assertEqual(revise.status_code, 409)
                self.assertEqual(regenerate.status_code, 409)
                self.assertEqual(revise.json()["error"]["code"], "ACTION_NOT_ALLOWED")
                self.assertEqual(regenerate.json()["error"]["code"], "ACTION_NOT_ALLOWED")
                self.assertFalse(self.runtime_root.exists())

    def test_11_capability_unavailable_creates_no_task(self):
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import CreativeActionService

        unavailable = CapabilityService(environment={}, which=lambda _name: None)
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            unavailable,
            self.lock_manager,
        )
        for response in (self.post_revise(), self.post_regenerate()):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "CAPABILITY_UNAVAILABLE")
        self.assertFalse(self.runtime_root.exists())

    def test_12_any_active_write_task_returns_project_busy(self):
        self.create_active_task()
        for response in (self.post_revise(), self.post_regenerate()):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            1,
        )

    def test_13_different_projects_can_run_revision_tasks_independently(self):
        release = Event()

        def blocked(*_args, **_kwargs):
            release.wait(timeout=2)
            return revised_brief()

        with patch("creative_workflow.revise_creative_brief", side_effect=blocked):
            first = self.post_revise("project-a")
            second = self.post_revise("project-b")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            release.set()
            first_task = self.wait_terminal(first.json()["task_id"])
            second_task = self.wait_terminal(second.json()["task_id"])
            self.assertEqual(
                first_task.status.value,
                "SUCCEEDED",
                first_task.error.model_dump() if first_task.error else None,
            )
            self.assertEqual(
                second_task.status.value,
                "SUCCEEDED",
                second_task.error.model_dump() if second_task.error else None,
            )

    def test_14_worker_uses_project_lock_and_revalidates_state(self):
        service = self.application.state.creative_action_service
        with (
            patch.object(
                self.lock_manager,
                "project_write",
                wraps=self.lock_manager.project_write,
            ) as project_write,
            patch.object(
                service,
                "_require_revise_allowed",
                wraps=service._require_revise_allowed,
            ) as validator,
            patch(
                "creative_workflow.revise_creative_brief",
                return_value=revised_brief(),
            ),
        ):
            response = self.post_revise()
            self.wait_terminal(response.json()["task_id"])
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_15_worker_race_rejection_never_calls_deepseek(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with (
            patch.object(
                service,
                "_require_regenerate_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("creative_workflow.generate_creative_brief", provider),
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(validator.call_count, 2)
        provider.assert_not_called()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "ACTION_NOT_ALLOWED")

    def test_16_restart_interrupts_both_operations_without_replay(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        for suffix, project_id, operation in (
            ("c", "project-a", TaskOperation.CREATIVE_REVISE),
            ("d", "project-b", TaskOperation.CREATIVE_REGENERATE),
        ):
            self.application.state.task_repository.create(
                TaskRecord(
                    task_id="task_" + suffix * 32,
                    project_id=project_id,
                    operation=operation,
                    status=TaskStatus.RUNNING,
                    created_at=now,
                    started_at=now,
                    correlation_id=f"req_restart_{suffix}",
                )
            )
        revise = Mock(side_effect=AssertionError("restart must not replay"))
        regenerate = Mock(side_effect=AssertionError("restart must not replay"))
        with (
            patch("creative_workflow.revise_creative_brief", revise),
            patch("creative_workflow.generate_creative_brief", regenerate),
        ):
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
        self.assertEqual({item.status.value for item in interrupted}, {"INTERRUPTED"})
        self.assertEqual(len(interrupted), 2)
        revise.assert_not_called()
        regenerate.assert_not_called()

    def test_17_shared_core_callables_are_used_by_web_once(self):
        from creative_workflow import regenerate_creative_stage, revise_creative_stage

        with (
            patch(
                "creative_workflow.revise_creative_stage",
                wraps=revise_creative_stage,
            ) as revise_shared,
            patch(
                "creative_workflow.revise_creative_brief",
                return_value=revised_brief(),
            ),
        ):
            response = self.post_revise()
            self.wait_terminal(response.json()["task_id"])
        revise_shared.assert_called_once()

        with (
            patch(
                "creative_workflow.regenerate_creative_stage",
                wraps=regenerate_creative_stage,
            ) as regenerate_shared,
            patch(
                "creative_workflow.generate_creative_brief",
                return_value=revised_brief("新方案"),
            ),
        ):
            response = self.post_regenerate()
            self.wait_terminal(response.json()["task_id"])
        regenerate_shared.assert_called_once()

    def test_18_no_network_minimax_tts_ffmpeg_or_storyboard_is_invoked(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("provider")),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch("storyboard.generate_storyboard", side_effect=AssertionError("storyboard")),
            patch("video_generator.generate_video", side_effect=AssertionError("minimax")),
            patch("voice_generation.generate_confirmed_voice", side_effect=AssertionError("tts")),
            patch("creative_workflow.revise_creative_brief", return_value=revised_brief()),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")
        assert_public_payload(self, response.json())

    def test_19_openapi_uses_endpoint_specific_creative_task_examples(self):
        schema = self.client.get("/openapi.json").json()
        expected_operations = {
            "/api/projects/{project_id}/planning/creative/generate": (
                "CREATIVE_GENERATE"
            ),
            "/api/projects/{project_id}/planning/creative/retry": "CREATIVE_RETRY",
            "/api/projects/{project_id}/planning/creative/revise": "CREATIVE_REVISE",
            "/api/projects/{project_id}/planning/creative/regenerate": (
                "CREATIVE_REGENERATE"
            ),
        }

        for path, expected_operation in expected_operations.items():
            response = schema["paths"][path]["post"]["responses"]["202"]
            media_type = response["content"]["application/json"]
            self.assertEqual(
                media_type["schema"],
                {"$ref": "#/components/schemas/TaskRecord"},
            )
            self.assertEqual(media_type["example"]["operation"], expected_operation)

        operation_schema = schema["components"]["schemas"]["TaskOperation"]
        self.assertNotIn("default", operation_schema)
        self.assertNotIn("example", operation_schema)

    def test_20_project_replace_retry_never_repeats_core_or_provider(self):
        from creative_workflow import revise_creative_stage

        original_replace = os.replace
        project_file = (self.project_a / "project.json").resolve()
        failed_once = False
        project_replace_attempts = 0

        def fail_first_project_replace(source, target):
            nonlocal failed_once, project_replace_attempts
            if Path(target).resolve() == project_file:
                project_replace_attempts += 1
                if not failed_once:
                    failed_once = True
                    error = PermissionError(13, "simulated Windows replace conflict")
                    error.winerror = 5
                    raise error
            return original_replace(source, target)

        provider = Mock(return_value=revised_brief())
        with (
            patch(
                "creative_workflow.revise_creative_stage",
                wraps=revise_creative_stage,
            ) as core_revise,
            patch("creative_workflow.revise_creative_brief", provider),
            patch(
                "project_manager.os.replace",
                side_effect=fail_first_project_replace,
            ),
            patch("project_manager.time.sleep"),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])

        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertGreaterEqual(project_replace_attempts, 3)
        core_revise.assert_called_once()
        provider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
