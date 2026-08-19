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
    write_json,
    write_project,
)
from tests.web.test_backend_phase_3a2_creative_generate import creative_brief
from tests.web.test_backend_phase_3b1_storyboard_generate import storyboard_result
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase3C2AVideoPromptApproveTests(unittest.TestCase):
    def setUp(self) -> None:
        from storyboard import (
            ShotVideoPrompt,
            VideoPromptPlan,
            apply_video_overlay_constraints,
        )
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
        self.board = storyboard_result()
        self.brief = creative_brief()
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(
                    shot_id=shot.shot_id,
                    visual_prompt_core=f"commercial product core {shot.shot_id}",
                    video_prompt=apply_video_overlay_constraints(
                        f"commercial product core {shot.shot_id}",
                        shot,
                        self.brief.global_constraints,
                    ),
                )
                for shot in self.board.shots
            ]
        )
        self.storyboard_path = self.project_dir / "storyboard" / "storyboard.json"
        self.prompts_path = self.project_dir / "storyboard" / "video_prompts.json"
        self.progress_path = (
            self.project_dir / "storyboard" / "video_prompt_generation_progress.json"
        )
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            self.brief.model_dump(),
        )
        write_json(self.storyboard_path, self.board.model_dump())
        write_json(self.prompts_path, self.plan.model_dump())
        write_json(self.progress_path, {"status": "COMPLETED", "sentinel": True})
        # Match a real current-schema project before measuring approval writes.
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint

        ProjectCheckpoint.load(
            create_project_paths(self.project_dir, ensure_directories=False)
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
        project = base_project(project_id="project-a", project_name="提示词审核测试")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["stages"]["PROMPT_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "WAITING_REVIEW"
        return write_project(self.projects_root, "project-a", project)

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/video-prompts/approve",
            headers={"X-Correlation-ID": "req_phase3c2a_video_prompt"},
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

    def test_01_waiting_review_approves_shared_core_and_binds_active_versions(self):
        from video_prompt_workflow import approve_video_prompts_stage

        prompt_sha = hashlib.sha256(self.prompts_path.read_bytes()).hexdigest()
        storyboard_sha = hashlib.sha256(self.storyboard_path.read_bytes()).hexdigest()
        progress_before = self.progress_path.read_bytes()
        progress_mtime = self.progress_path.stat().st_mtime_ns
        task_count = self.task_count()
        with patch(
            "video_prompt_workflow.approve_video_prompts_stage",
            wraps=approve_video_prompts_stage,
        ) as shared:
            response = self.post()

        self.assertEqual(response.status_code, 200)
        shared.assert_called_once()
        payload = response.json()
        self.assertEqual(payload["workflow_phase"], "VIDEO_GENERATION")
        self.assertEqual(payload["status"], "APPROVED")
        self.assertEqual(payload["stages"]["video_prompt"]["status"], "APPROVED")
        self.assertEqual(payload["stages"]["shots"]["status"], "NOT_STARTED")
        self.assertEqual(payload["available_actions"], ["GENERATE_SHOTS"])
        project = self.read_project()
        self.assertEqual(project["stages"]["PROMPT_REVIEW"]["status"], "APPROVED")
        self.assertEqual(project["current_stage"], "PROMPT_REVIEW")
        for shot_id in (1, 2, 3):
            entry = project["video_generation"]["shots"][str(shot_id)]
            self.assertEqual(entry["active_prompt_version"], 1)
            self.assertIsNone(entry["approved_prompt_version"])
            self.assertIsNone(entry["active_video_version"])
            self.assertEqual(entry["status"], "NOT_STARTED")
        self.assertEqual(self.task_count(), task_count)
        self.assertFalse(self.runtime_root.exists())
        self.assertEqual(
            hashlib.sha256(self.prompts_path.read_bytes()).hexdigest(), prompt_sha
        )
        self.assertEqual(
            hashlib.sha256(self.storyboard_path.read_bytes()).hexdigest(), storyboard_sha
        )
        self.assertEqual(self.progress_path.read_bytes(), progress_before)
        self.assertEqual(self.progress_path.stat().st_mtime_ns, progress_mtime)
        self.assertFalse(any(self.project_dir.rglob("*.mp4")))

        content = self.client.get(
            "/api/projects/project-a/planning/video-prompts"
        ).json()["content"]
        self.assertEqual([shot["prompt_version"] for shot in content["shots"]], [1, 1, 1])
        self.assertEqual(
            [shot["prompt_source"] for shot in content["shots"]],
            ["ai_generated", "ai_generated", "ai_generated"],
        )
        self.assertEqual(
            [shot["prompt_text"] for shot in content["shots"]],
            [shot.video_prompt for shot in self.plan.shots],
        )

    def test_02_mixed_matching_prompt_versions_are_preserved(self):
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint

        paths = create_project_paths(self.project_dir, ensure_directories=False)
        checkpoint = ProjectCheckpoint.load(paths)
        checkpoint.ensure_shots([1, 2, 3])
        expected = {1: 2, 2: 1, 3: 4}
        for item in self.plan.shots:
            version = expected[item.shot_id]
            checkpoint.save_prompt_version(
                item.shot_id,
                {
                    "shot_id": item.shot_id,
                    "version": version,
                    "source": "ai_revision" if version > 1 else "ai_generated",
                    "created_at": "2026-08-19T00:00:00+08:00",
                    "prompt": item.video_prompt,
                },
            )

        response = self.post()

        self.assertEqual(response.status_code, 200)
        project = self.read_project()
        for shot_id, version in expected.items():
            entry = project["video_generation"]["shots"][str(shot_id)]
            self.assertEqual(entry["active_prompt_version"], version)
            self.assertIsNone(entry["approved_prompt_version"])
        content = self.client.get(
            "/api/projects/project-a/planning/video-prompts"
        ).json()["content"]
        self.assertEqual(
            [shot["prompt_version"] for shot in content["shots"]],
            [2, 1, 4],
        )

    def test_03_incomplete_duplicate_and_stale_prompt_binding_are_rejected(self):
        original_project = self.read_project()
        invalid = (
            {"shots": self.plan.model_dump()["shots"][:2]},
            {
                "shots": [
                    self.plan.model_dump()["shots"][0],
                    self.plan.model_dump()["shots"][0],
                    self.plan.model_dump()["shots"][2],
                ]
            },
        )
        for payload in invalid:
            with self.subTest(ids=[shot["shot_id"] for shot in payload["shots"]]):
                write_json(self.prompts_path, payload)
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
                self.assertEqual(self.read_project(), original_project)

        write_json(self.prompts_path, self.plan.model_dump())
        stale = self.read_project()
        stale["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "active_prompt_version": 7,
                "prompt_version_count": 7,
                "prompt_versions": [
                    {"shot_id": 1, "version": 7, "prompt": "unrelated stale prompt"}
                ],
            }
        }
        self.write_project_data(stale)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(
            self.read_project()["stages"]["PROMPT_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_04_not_started_and_repeated_approval_are_rejected(self):
        for prompt_status, review_status in (
            ("NOT_STARTED", "NOT_STARTED"),
            ("COMPLETED", "APPROVED"),
        ):
            with self.subTest(prompt_status=prompt_status, review_status=review_status):
                project = self.read_project()
                project["stages"]["VIDEO_PROMPT"]["status"] = prompt_status
                project["stages"]["PROMPT_REVIEW"]["status"] = review_status
                project["current_stage"] = "VIDEO_PROMPT" if prompt_status == "NOT_STARTED" else "PROMPT_REVIEW"
                project["status"] = review_status
                self.write_project_data(project)
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(self.task_count(), 0)

    def test_05_active_task_returns_project_busy_without_approval(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.VIDEO_PROMPT_GENERATE,
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_active",
            )
        )
        before = self.read_project()
        core = Mock(side_effect=AssertionError("approval must not run"))
        with patch("video_prompt_workflow.approve_video_prompts_stage", core):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        core.assert_not_called()
        self.assertEqual(self.read_project(), before)
        self.assertEqual(self.task_count(), 1)

    def test_06_project_lock_and_lock_scoped_revalidation_are_used(self):
        service = self.application.state.creative_action_service
        with (
            patch.object(
                self.lock_manager,
                "project_write",
                wraps=self.lock_manager.project_write,
            ) as project_write,
            patch.object(
                service,
                "_require_video_prompt_approve_allowed",
                wraps=service._require_video_prompt_approve_allowed,
            ) as validator,
        ):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_07_race_change_inside_lock_rejects_without_core(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        core = Mock(side_effect=AssertionError("approval must not run"))
        with (
            patch.object(
                service,
                "_require_video_prompt_approve_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("video_prompt_workflow.approve_video_prompts_stage", core),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertEqual(validator.call_count, 2)
        core.assert_not_called()
        self.assertEqual(
            self.read_project()["stages"]["PROMPT_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_08_project_errors_and_response_are_safe(self):
        for project_id, status, code in (
            ("missing", 404, "PROJECT_NOT_FOUND"),
            ("C:unsafe", 422, "INVALID_PROJECT_ID"),
        ):
            with self.subTest(project_id=project_id):
                response = self.post(project_id)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
                assert_public_payload(self, response.json())

        response = self.post()
        self.assertEqual(response.status_code, 200)
        assert_public_payload(self, response.json())
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (
            "must-not-be-read",
            str(self.project_dir),
            "video_prompts.json",
            "approved_prompt_version",
            "active_prompt_version",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_09_approval_invokes_no_provider_process_task_or_video(self):
        task_submit = Mock(side_effect=AssertionError("task must not be submitted"))
        with (
            patch.object(self.application.state.task_service, "submit", task_submit),
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
                "storyboard.generate_video_prompts",
                side_effect=AssertionError("DeepSeek must not be used"),
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
        self.assertEqual(self.task_count(), 0)
        self.assertFalse(self.runtime_root.exists())
        self.assertFalse(any(self.project_dir.rglob("*.mp4")))

    def test_10_openapi_documents_synchronous_workflow_response(self):
        operation = self.client.get("/openapi.json").json()["paths"][
            "/api/projects/{project_id}/planning/video-prompts/approve"
        ]["post"]
        self.assertIn("200", operation["responses"])
        self.assertNotIn("202", operation["responses"])
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["$ref"], "#/components/schemas/ProjectWorkflowResponse")


if __name__ == "__main__":
    unittest.main()
