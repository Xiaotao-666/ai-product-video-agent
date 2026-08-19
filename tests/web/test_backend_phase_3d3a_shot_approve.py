from __future__ import annotations

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

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from shot_storage import ensure_bundle_placeholders, sync_shot_manifest_from_checkpoint
from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase3D3AShotApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.project_dir = self._write_waiting_project()
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

    def _write_waiting_project(self) -> Path:
        project = base_project(project_id="project-a", project_name="Shot approve")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "WAITING_REVIEW",
                "generation_phase": "WAITING_REVIEW",
                "generation_count": 1,
                "active_prompt_version": 2,
                "approved_prompt_version": None,
                "active_video_version": 1,
                "approved_video_version": None,
                "pending_video_version": None,
                "current_generation_version": 1,
                "submission_unknown": False,
                "prompt_versions": [
                    {
                        "shot_id": 1,
                        "version": 2,
                        "prompt": "active prompt",
                        "source": "ai_revision",
                    }
                ],
                "generation_versions": [
                    {
                        "video_version": 1,
                        "prompt_version": 2,
                        "status": "WAITING_REVIEW",
                        "review_result": "WAITING_REVIEW",
                        "generation_phase": "WAITING_REVIEW",
                        "provider": "minimax",
                        "provider_task_id": "private-provider-task",
                        "file_id": "private-file-id",
                        "provider_model": "MiniMax-Hailuo-2.3",
                        "generation_mode": "text_to_video",
                        "generation_count": 1,
                        "prompt_snapshot": {
                            "version": 2,
                            "prompt": "active prompt",
                            "source": "ai_revision",
                        },
                    }
                ],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        directory = write_project(self.projects_root, "project-a", project)
        paths = create_project_paths(directory)
        ensure_bundle_placeholders(
            paths,
            1,
            1,
            prompt_payload={"version": 2, "prompt": "active prompt", "source": "ai_revision"},
            generation_payload={
                "video_version": 1,
                "prompt_version": 2,
                "status": "WAITING_REVIEW",
                "generation_phase": "WAITING_REVIEW",
                "provider": "minimax",
                "provider_task_id": "private-provider-task",
                "file_id": "private-file-id",
                "provider_model": "MiniMax-Hailuo-2.3",
                "generation_mode": "text_to_video",
                "credential_env_name": "MINIMAX_API_KEY",
                "generation_count": 1,
            },
            review_result="WAITING_REVIEW",
        )
        paths.shot_version_video_path(1, 1).write_bytes(b"mock-video-v001")
        checkpoint = ProjectCheckpoint.load(paths)
        sync_shot_manifest_from_checkpoint(paths, 1, checkpoint.shot_checkpoint(1))
        return directory

    def post(self):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/approve",
            headers={"X-Correlation-ID": "req_shot_approve"},
        )

    def read_project(self) -> dict:
        return json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))

    def task_count(self) -> int:
        return len(self.application.state.task_repository.list_for_project("project-a"))

    def test_01_waiting_review_approves_through_shared_core_without_task(self) -> None:
        from shot_approval_workflow import approve_shot_stage

        before_tasks = self.task_count()
        with patch(
            "web_backend.services.shot_approval.approve_shot_stage",
            wraps=approve_shot_stage,
        ) as shared:
            response = self.post()

        self.assertEqual(response.status_code, 200)
        shared.assert_called_once()
        payload = response.json()
        self.assertEqual(payload["status"], "APPROVED")
        self.assertEqual(payload["official_version"], 1)
        self.assertIsNone(payload["pending_review_version"])
        self.assertEqual(payload["versions"][0]["role"], "OFFICIAL")
        self.assertEqual(payload["versions"][0]["review_status"], "APPROVED")
        self.assertEqual(self.task_count(), before_tasks)

        entry = self.read_project()["video_generation"]["shots"]["1"]
        self.assertEqual(entry["active_video_version"], 1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["active_prompt_version"], 2)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["candidate"]["status"], "NONE")

    def test_02_only_expected_metadata_changes_and_history_is_retained(self) -> None:
        before_dirs, before_files = tree_snapshot(self.project_dir)
        review_path = self.project_dir / "shots" / "shot_01" / "v001" / "review.json"
        review_before = json.loads(review_path.read_text(encoding="utf-8"))

        response = self.post()
        after_dirs, after_files = tree_snapshot(self.project_dir)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(after_dirs, before_dirs)
        self.assertEqual(set(after_files), set(before_files))
        changed = {
            path for path in before_files if after_files[path] != before_files[path]
        }
        self.assertEqual(changed, {"project.json", "shots/shot_01/shot.json", "shots/shot_01/v001/review.json"})
        for immutable in ("video.mp4", "prompt.json", "safety.json", "generation.json"):
            relative = f"shots/shot_01/v001/{immutable}"
            self.assertEqual(after_files[relative], before_files[relative])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        self.assertEqual(review["history"][:-1], review_before["history"])
        self.assertEqual(review["history"][-1]["review_result"], "APPROVED")
        self.assertEqual(review["history"][-1]["user_action"], "approve")

    def test_03_invalid_states_and_missing_version_or_video_are_rejected(self) -> None:
        project = self.read_project()
        entry = project["video_generation"]["shots"]["1"]
        entry["status"] = "NOT_STARTED"
        (self.project_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")

    def test_04_failed_no_version_no_video_and_repeated_approval_are_rejected(self) -> None:
        project = self.read_project()
        project["video_generation"]["shots"]["1"]["status"] = "FAILED"
        (self.project_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.assertEqual(self.post().json()["error"]["code"], "ACTION_NOT_ALLOWED")

        project["video_generation"]["shots"]["1"]["status"] = "WAITING_REVIEW"
        project["video_generation"]["shots"]["1"]["active_video_version"] = None
        (self.project_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.assertEqual(self.post().json()["error"]["code"], "ACTION_NOT_ALLOWED")

        project["video_generation"]["shots"]["1"]["active_video_version"] = 1
        (self.project_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.project_dir / "shots" / "shot_01" / "v001" / "video.mp4").unlink()
        self.assertEqual(self.post().json()["error"]["code"], "ACTION_NOT_ALLOWED")

    def test_05_repeated_approval_is_rejected_and_approved_generation_cannot_resume(self) -> None:
        self.assertEqual(self.post().status_code, 200)
        repeated = self.post()
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "APPROVED")
        self.assertFalse(status.json()["resume_available"])
        self.assertIsNone(status.json()["resume_kind"])
        resume = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/resume"
        )
        self.assertEqual(resume.status_code, 409)
        self.assertEqual(resume.json()["error"]["code"], "GENERATION_NOT_RESUMABLE")
        self.assertEqual(self.task_count(), 0)

    def test_06_active_task_blocks_approval_without_creating_another_task(self) -> None:
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.SHOT_GENERATE,
                target_id="shot_01",
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_active",
            )
        )
        before = self.read_project()
        core = Mock(side_effect=AssertionError("approval must not run"))
        with patch("web_backend.services.shot_approval.approve_shot_stage", core):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        core.assert_not_called()
        self.assertEqual(self.task_count(), 1)
        self.assertEqual(self.read_project(), before)

    def test_07_project_lock_and_lock_scoped_revalidation_close_races(self) -> None:
        service = self.application.state.shot_approval_service
        with (
            patch.object(
                self.lock_manager, "project_write", wraps=self.lock_manager.project_write
            ) as project_write,
            patch.object(
                service,
                "_require_approve_allowed",
                wraps=service._require_approve_allowed,
            ) as validator,
        ):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_08_race_revalidation_rejects_without_any_approval_write(self) -> None:
        from web_backend.services.shot_approval import ShotApprovalNotAllowed

        service = self.application.state.shot_approval_service
        before = tree_snapshot(self.project_dir)
        core = Mock(side_effect=AssertionError("approval must not run"))
        with (
            patch.object(
                service,
                "_require_approve_allowed",
                side_effect=[None, ShotApprovalNotAllowed("race")],
            ) as validator,
            patch("web_backend.services.shot_approval.approve_shot_stage", core),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(validator.call_count, 2)
        core.assert_not_called()
        self.assertEqual(tree_snapshot(self.project_dir), before)

    def test_09_no_provider_network_process_or_task_submission_and_safe_response(self) -> None:
        task_submit = Mock(side_effect=AssertionError("task must not be submitted"))
        with (
            patch.object(self.application.state.task_service, "submit", task_submit),
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("provider")),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        task_submit.assert_not_called()
        assert_public_payload(self, response.json())
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (
            str(self.project_dir),
            "private-provider-task",
            "private-file-id",
            "MINIMAX_API_KEY",
            "credential",
            "provider_task_id",
            "file_id",
        ):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
