from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.test_backend_phase_3b1_storyboard_generate import storyboard_result
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase3B2AStoryboardApproveTests(unittest.TestCase):
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
        self.project_dir = self._write_waiting_project()
        self.storyboard_path = self.project_dir / "storyboard" / "storyboard.json"
        write_json(self.storyboard_path, storyboard_result().model_dump())
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            {"canonical": "creative-must-not-change"},
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
            environment={"DEEPSEEK_API_KEY": "must-not-be-read"},
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

    def _write_waiting_project(self) -> Path:
        project = base_project(project_id="project-a", project_name="审核测试项目")
        project["stages"]["CREATIVE"]["status"] = "COMPLETED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        project["stages"]["STORYBOARD"]["status"] = "COMPLETED"
        project["stages"]["STORYBOARD_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "STORYBOARD_REVIEW"
        project["status"] = "WAITING_REVIEW"
        return write_project(self.projects_root, "project-a", project)

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/storyboard/approve",
            headers={"X-Correlation-ID": "req_phase3b2a_storyboard"},
        )

    def read_project(self) -> dict:
        return json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )

    def write_project_data(self, payload: dict) -> None:
        write_json(self.project_dir / "project.json", payload)

    def task_count(self) -> int:
        return len(
            self.application.state.task_repository.list_for_project("project-a")
        )

    def test_01_waiting_review_approves_through_shared_core_without_task(self):
        from storyboard_workflow import approve_storyboard_stage

        before_sha = hashlib.sha256(self.storyboard_path.read_bytes()).hexdigest()
        before_task_count = self.task_count()
        with patch(
            "storyboard_workflow.approve_storyboard_stage",
            wraps=approve_storyboard_stage,
        ) as shared:
            response = self.post()

        self.assertEqual(response.status_code, 200)
        shared.assert_called_once()
        payload = response.json()
        self.assertEqual(payload["workflow_phase"], "VIDEO_PROMPT")
        self.assertEqual(payload["status"], "APPROVED")
        self.assertEqual(payload["stages"]["storyboard"]["status"], "APPROVED")
        self.assertEqual(payload["stages"]["video_prompt"]["status"], "NOT_STARTED")
        self.assertEqual(payload["available_actions"], ["GENERATE_VIDEO_PROMPTS"])
        project = self.read_project()
        self.assertEqual(project["stages"]["STORYBOARD"]["status"], "COMPLETED")
        self.assertEqual(
            project["stages"]["STORYBOARD_REVIEW"]["status"], "APPROVED"
        )
        self.assertEqual(project["current_stage"], "STORYBOARD_REVIEW")
        self.assertEqual(self.task_count(), before_task_count)
        self.assertFalse(self.runtime_root.exists())
        self.assertEqual(
            hashlib.sha256(self.storyboard_path.read_bytes()).hexdigest(),
            before_sha,
        )
        self.assertFalse(
            (self.project_dir / "storyboard" / "video_prompts.json").exists()
        )

    def test_02_only_project_state_changes_and_response_is_public(self):
        before_dirs, before_files = tree_snapshot(self.project_dir)
        response = self.post()
        after_dirs, after_files = tree_snapshot(self.project_dir)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(after_dirs, before_dirs)
        self.assertEqual(set(after_files), set(before_files))
        for relative_path, before in before_files.items():
            if relative_path == "project.json":
                continue
            self.assertEqual(after_files[relative_path][0], before[0])
        assert_public_payload(self, response.json())
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (
            "must-not-be-read",
            str(self.project_dir),
            "creative_brief.json",
            "storyboard.json",
            '"current_stage"',
            '"STORYBOARD_REVIEW"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_03_not_started_and_repeated_approval_are_action_not_allowed(self):
        for storyboard_status, review_status in (
            ("NOT_STARTED", "NOT_STARTED"),
            ("COMPLETED", "APPROVED"),
        ):
            with self.subTest(
                storyboard_status=storyboard_status,
                review_status=review_status,
            ):
                project = self.read_project()
                project["stages"]["STORYBOARD"]["status"] = storyboard_status
                project["stages"]["STORYBOARD_REVIEW"]["status"] = review_status
                project["current_stage"] = (
                    "STORYBOARD" if storyboard_status == "NOT_STARTED" else "STORYBOARD_REVIEW"
                )
                project["status"] = review_status
                self.write_project_data(project)
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(
                    response.json()["error"]["code"],
                    "ACTION_NOT_ALLOWED",
                )
        self.assertFalse(self.runtime_root.exists())

    def test_04_active_task_returns_project_busy_without_approval_or_new_task(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.STORYBOARD_GENERATE,
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_active",
            )
        )
        before = self.read_project()
        core = Mock(side_effect=AssertionError("approval must not run"))
        with patch("storyboard_workflow.approve_storyboard_stage", core):
            response = self.post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        core.assert_not_called()
        self.assertEqual(self.task_count(), 1)
        self.assertEqual(self.read_project(), before)

    def test_05_project_lock_and_lock_scoped_revalidation_are_used(self):
        service = self.application.state.creative_action_service
        with (
            patch.object(
                self.lock_manager,
                "project_write",
                wraps=self.lock_manager.project_write,
            ) as project_write,
            patch.object(
                service,
                "_require_storyboard_approve_allowed",
                wraps=service._require_storyboard_approve_allowed,
            ) as validator,
        ):
            response = self.post()

        self.assertEqual(response.status_code, 200)
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_06_race_change_inside_lock_rejects_without_core_approval(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        core = Mock(side_effect=AssertionError("approval must not run"))
        with (
            patch.object(
                service,
                "_require_storyboard_approve_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("storyboard_workflow.approve_storyboard_stage", core),
        ):
            response = self.post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(validator.call_count, 2)
        core.assert_not_called()
        self.assertEqual(
            self.read_project()["stages"]["STORYBOARD_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_07_lock_is_released_after_unexpected_core_failure(self):
        with patch(
            "storyboard_workflow.approve_storyboard_stage",
            side_effect=RuntimeError("simulated approval failure"),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 500)

        with self.lock_manager.project_write("project-a"):
            pass
        self.assertEqual(
            self.read_project()["stages"]["STORYBOARD_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_08_project_errors_are_safe_and_create_no_runtime(self):
        for project_id, status, code in (
            ("missing", 404, "PROJECT_NOT_FOUND"),
            ("C:unsafe", 422, "INVALID_PROJECT_ID"),
        ):
            with self.subTest(project_id=project_id):
                response = self.post(project_id)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
                assert_public_payload(self, response.json())
        self.assertFalse(self.runtime_root.exists())

    def test_09_approval_invokes_no_provider_network_process_or_task_runner(self):
        task_submit = Mock(side_effect=AssertionError("task must not be submitted"))
        with (
            patch.object(
                self.application.state.task_service,
                "submit",
                task_submit,
            ),
            patch.object(
                self.capabilities,
                "deepseek_api_key",
                side_effect=AssertionError("credential must not be read"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network must not be used"),
            ),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider must not be used"),
            ),
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("process must not be used"),
            ),
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("process must not be used"),
            ),
            patch(
                "storyboard.generate_storyboard",
                side_effect=AssertionError("DeepSeek must not be used"),
            ),
            patch(
                "storyboard.generate_video_prompts",
                side_effect=AssertionError("Video Prompt must not be generated"),
            ),
            patch(
                "video_generator.generate_video",
                side_effect=AssertionError("MiniMax must not be used"),
            ),
            patch(
                "voice_generation.generate_confirmed_voice",
                side_effect=AssertionError("TTS must not be used"),
            ),
        ):
            response = self.post()

        self.assertEqual(response.status_code, 200)
        task_submit.assert_not_called()
        self.assertFalse(self.runtime_root.exists())
        self.assertFalse(
            (self.project_dir / "storyboard" / "video_prompts.json").exists()
        )

    def test_10_no_storyboard_approve_task_operation_exists(self):
        from web_backend.models.tasks import TaskOperation

        self.assertNotIn(
            "STORYBOARD_APPROVE",
            {operation.value for operation in TaskOperation},
        )


if __name__ == "__main__":
    unittest.main()
