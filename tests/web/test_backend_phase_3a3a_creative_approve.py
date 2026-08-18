from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase3A3ACreativeApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        project = base_project(project_id="project-a")
        project["stages"]["CREATIVE"]["status"] = "COMPLETED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "CREATIVE_REVIEW"
        project["status"] = "WAITING_REVIEW"
        self.project_dir = write_project(
            self.projects_root,
            "project-a",
            project,
        )
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            {"creative_concept": "只用于本地测试的 Creative"},
        )
        self.lock_manager = ProjectLockManager()
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            ),
            lock_manager=self.lock_manager,
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    @property
    def project_file(self) -> Path:
        return self.project_dir / "project.json"

    def read_project(self) -> dict:
        return json.loads(self.project_file.read_text(encoding="utf-8"))

    def write_project_data(self, data: dict) -> None:
        write_json(self.project_file, data)

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/creative/approve",
            headers={"X-Correlation-ID": "req_phase3a3a"},
        )

    def create_active_task(self, *, status: str = "QUEUED") -> None:
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.CREATIVE_GENERATE,
                status=TaskStatus(status),
                created_at=now,
                started_at=now if status == "RUNNING" else None,
                correlation_id="req_active_task",
            )
        )

    def test_01_waiting_review_approve_returns_latest_storyboard_workflow(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["project_id"], "project-a")
        self.assertEqual(payload["workflow_phase"], "STORYBOARD")
        self.assertEqual(payload["stages"]["creative"]["status"], "APPROVED")
        self.assertEqual(payload["stages"]["storyboard"]["status"], "NOT_STARTED")
        self.assertEqual(payload["available_actions"], ["GENERATE_STORYBOARD"])
        self.assertEqual(response.headers["X-Correlation-ID"], "req_phase3a3a")

    def test_02_endpoint_calls_shared_core_callable_exactly_once(self):
        from creative_workflow import approve_creative_stage

        with patch(
            "creative_workflow.approve_creative_stage",
            wraps=approve_creative_stage,
        ) as shared:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        shared.assert_called_once()

    def test_03_persists_only_approval_and_does_not_generate_storyboard(self):
        creative_before = (self.project_dir / "concepts" / "creative_brief.json").read_bytes()
        paths_before = {
            path.relative_to(self.project_dir).as_posix()
            for path in self.project_dir.rglob("*")
        }
        response = self.post()
        self.assertEqual(response.status_code, 200)
        project = self.read_project()
        self.assertEqual(project["stages"]["CREATIVE"]["status"], "COMPLETED")
        self.assertEqual(project["stages"]["CREATIVE_REVIEW"]["status"], "APPROVED")
        self.assertEqual(project["stages"]["STORYBOARD"]["status"], "NOT_STARTED")
        self.assertEqual(project["current_stage"], "CREATIVE_REVIEW")
        self.assertEqual(project["status"], "APPROVED")
        self.assertEqual(
            (self.project_dir / "concepts" / "creative_brief.json").read_bytes(),
            creative_before,
        )
        self.assertFalse((self.project_dir / "storyboard").exists())
        self.assertEqual(
            {
                path.relative_to(self.project_dir).as_posix()
                for path in self.project_dir.rglob("*")
            },
            paths_before,
        )

    def test_04_approve_runs_no_provider_network_or_media_pipeline(self):
        mocks = [Mock(side_effect=AssertionError("must not run")) for _ in range(6)]
        with (
            patch("storyboard.generate_creative_brief", mocks[0]),
            patch("storyboard.generate_storyboard", mocks[1]),
            patch("video_generator.generate_video", mocks[2]),
            patch("voice_generation.generate_confirmed_voice", mocks[3]),
            patch("video_assembly.assemble_approved_shots", mocks[4]),
            patch("requests.sessions.Session.request", mocks[5]),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        for mocked in mocks:
            mocked.assert_not_called()

    def test_05_approve_creates_no_task_or_runtime_storage(self):
        before = self.application.state.task_repository.list_for_project("project-a")
        self.assertFalse(self.runtime_root.exists())
        response = self.post()
        after = self.application.state.task_repository.list_for_project("project-a")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, after)
        self.assertFalse(self.runtime_root.exists())

    def test_06_invalid_states_and_repeat_are_safe_action_not_allowed(self):
        with self.subTest("not started"):
            project = self.read_project()
            project["stages"]["CREATIVE"]["status"] = "NOT_STARTED"
            project["stages"]["CREATIVE_REVIEW"]["status"] = "NOT_STARTED"
            project["current_stage"] = "CREATIVE"
            project["status"] = "NOT_STARTED"
            self.write_project_data(project)
            response = self.post()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")

        with self.subTest("already approved"):
            project = self.read_project()
            project["stages"]["CREATIVE"]["status"] = "COMPLETED"
            project["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
            project["current_stage"] = "CREATIVE_REVIEW"
            project["status"] = "APPROVED"
            self.write_project_data(project)
            response = self.post()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")

    def test_07_active_queued_or_running_task_returns_project_busy(self):
        for status in ("QUEUED", "RUNNING"):
            with self.subTest(status=status):
                if self.runtime_root.exists():
                    for task in self.runtime_root.rglob("*.json"):
                        task.unlink()
                self.create_active_task(status=status)
                before = self.project_file.read_bytes()
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
                self.assertEqual(self.project_file.read_bytes(), before)

    def test_08_project_write_lock_is_acquired(self):
        with patch.object(
            self.lock_manager,
            "project_write",
            wraps=self.lock_manager.project_write,
        ) as project_write:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        project_write.assert_called_once_with("project-a")

    def test_09_busy_project_lock_returns_project_busy_without_writes(self):
        before = tree_snapshot(self.project_dir)
        with self.lock_manager.project_write("project-a"):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertEqual(tree_snapshot(self.project_dir), before)

    def test_10_state_is_validated_before_and_inside_the_lock(self):
        service = self.application.state.creative_action_service
        with patch.object(
            service,
            "_require_approve_allowed",
            wraps=service._require_approve_allowed,
        ) as validate:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(validate.call_count, 2)

    def test_11_race_state_change_is_rejected_and_lock_is_released(self):
        service = self.application.state.creative_action_service
        original = service._require_approve_allowed
        calls = 0

        def change_after_preflight(project_id: str) -> None:
            nonlocal calls
            original(project_id)
            calls += 1
            if calls == 1:
                project = self.read_project()
                project["stages"]["CREATIVE_REVIEW"]["status"] = "NOT_STARTED"
                self.write_project_data(project)

        with patch.object(
            service,
            "_require_approve_allowed",
            side_effect=change_after_preflight,
        ):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(
            self.read_project()["stages"]["CREATIVE_REVIEW"]["status"],
            "NOT_STARTED",
        )

        project = self.read_project()
        project["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        self.write_project_data(project)
        self.assertEqual(self.post().status_code, 200)

    def test_12_unexpected_core_error_releases_lock_without_state_change(self):
        with patch(
            "creative_workflow.approve_creative_stage",
            side_effect=RuntimeError("private D:\\secret API_KEY"),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "UNEXPECTED_ERROR")
        self.assertNotIn("D:\\secret", response.text)
        self.assertEqual(
            self.read_project()["stages"]["CREATIVE_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        self.assertEqual(self.post().status_code, 200)

    def test_13_missing_and_invalid_project_ids_are_safe(self):
        missing = self.post("missing-project")
        invalid = self.post("C:secret")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "PROJECT_NOT_FOUND")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "INVALID_PROJECT_ID")

    def test_14_response_is_public_dto_without_paths_secrets_or_raw_state(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        assert_public_payload(self, payload)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.projects_root), serialized)
        self.assertNotIn("API_KEY", serialized)
        self.assertNotIn("request", payload)
        self.assertNotIn("current_stage", payload)
        self.assertNotIn("revision_history", payload)

    def test_15_error_correlation_id_is_safe_and_preserved(self):
        self.assertEqual(self.post().status_code, 200)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["correlation_id"], "req_phase3a3a")
        self.assertNotIn(str(self.projects_root), response.text)


if __name__ == "__main__":
    unittest.main()
