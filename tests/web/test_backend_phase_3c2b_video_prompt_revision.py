from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    write_json,
    write_project,
)
from tests.web.test_backend_phase_3a2_creative_generate import creative_brief
from tests.web.test_backend_phase_3b1_storyboard_generate import storyboard_result
from tests.web.web_response_assertions import assert_public_payload


FEEDBACK = "减少镜头运动，让产品主体更稳定，保持无人物。"


class WebBackendPhase3C2BVideoPromptRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint
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
        project = base_project(project_id="project-a", project_name="提示词修改测试")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["stages"]["PROMPT_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "WAITING_REVIEW"
        self.project_dir = write_project(self.projects_root, "project-a", project)
        self.brief = creative_brief()
        self.board = storyboard_result()
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(
                    shot_id=shot.shot_id,
                    visual_prompt_core=f"old-core-{shot.shot_id}",
                    video_prompt=apply_video_overlay_constraints(
                        f"old-core-{shot.shot_id}",
                        shot,
                        self.brief.global_constraints,
                    ),
                )
                for shot in self.board.shots
            ]
        )
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            self.brief.model_dump(),
        )
        write_json(
            self.project_dir / "storyboard" / "storyboard.json",
            self.board.model_dump(),
        )
        write_json(
            self.project_dir / "storyboard" / "video_prompts.json",
            self.plan.model_dump(),
        )
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
            environment={"DEEPSEEK_API_KEY": "mock-video-prompt-key"},
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
        network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real provider/network call"),
        )
        network_guard.start()
        self.addCleanup(network_guard.stop)

    def post_revise(self, payload=None):
        return self.client.post(
            "/api/projects/project-a/planning/video-prompts/revise",
            json={"feedback": FEEDBACK} if payload is None else payload,
            headers={"X-Correlation-ID": "req_phase3c2b_revise"},
        )

    def post_regenerate(self):
        return self.client.post(
            "/api/projects/project-a/planning/video-prompts/regenerate",
            headers={"X-Correlation-ID": "req_phase3c2b_regenerate"},
        )

    def wait_terminal(self, task_id: str, timeout: float = 5.0):
        from web_backend.models.tasks import TERMINAL_TASK_STATUSES

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.application.state.task_repository.get(task_id)
            if task.status in TERMINAL_TASK_STATUSES:
                return task
            Event().wait(0.01)
        self.fail(f"task {task_id} did not become terminal")

    def read_project(self) -> dict:
        return json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )

    def set_review_status(self, status: str) -> None:
        project = self.read_project()
        project["stages"]["PROMPT_REVIEW"]["status"] = status
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = status
        write_json(self.project_dir / "project.json", project)

    def test_01_revise_is_durable_per_shot_and_feedback_is_not_in_task(self):
        seen: list[tuple[int, str | None, str | None]] = []

        def revise_one(_request, _brief, shot, *_args, **kwargs):
            seen.append(
                (
                    shot.shot_id,
                    kwargs.get("current_core"),
                    kwargs.get("revision_comment"),
                )
            )
            return f"revised-core-{shot.shot_id}"

        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=revise_one,
        ) as provider:
            response = self.post_revise()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers["location"], f"/api/tasks/{response.json()['task_id']}")
            task = self.wait_terminal(response.json()["task_id"])

        self.assertEqual(task.operation.value, "VIDEO_PROMPT_REVISE")
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(provider.call_count, len(self.board.shots))
        self.assertEqual(
            seen,
            [
                (shot.shot_id, f"old-core-{shot.shot_id}", FEEDBACK)
                for shot in self.board.shots
            ],
        )
        task_file = next((self.runtime_root / "tasks").glob(f"{task.task_id}*.json"))
        task_text = task_file.read_text(encoding="utf-8")
        self.assertNotIn(FEEDBACK, task_text)
        self.assertNotIn("revised-core", task_text)
        assert_public_payload(self, response.json())
        project = self.read_project()
        self.assertEqual(project["stages"]["PROMPT_REVIEW"]["status"], "WAITING_REVIEW")
        for shot in self.board.shots:
            entry = project["video_generation"]["shots"][str(shot.shot_id)]
            self.assertEqual(entry["active_prompt_version"], 2)
            self.assertIsNone(entry["approved_prompt_version"])
        self.assertFalse(any(self.project_dir.rglob("*.mp4")))

    def test_02_regenerate_is_clean_per_shot_and_does_not_reuse_initial_cache(self):
        progress = self.project_dir / "storyboard" / "video_prompt_generation_progress.json"
        write_json(
            progress,
            {
                "video_prompt_schema_version": 2,
                "storyboard_fingerprint": "initial-generate-cache",
                "operation": "generate",
                "status": "COMPLETED",
                "shots": [],
            },
        )
        seen: list[tuple[object, object]] = []

        def regenerate_one(_request, _brief, shot, *_args, **kwargs):
            seen.append(
                (kwargs.get("current_core"), kwargs.get("revision_comment"))
            )
            return f"fresh-core-{shot.shot_id}"

        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=regenerate_one,
        ) as provider:
            response = self.post_regenerate()
            self.assertEqual(response.status_code, 202)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.operation.value, "VIDEO_PROMPT_REGENERATE")
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(provider.call_count, len(self.board.shots))
        self.assertEqual(seen, [(None, None)] * len(self.board.shots))

    def test_03_request_validation_rejects_empty_oversized_and_extra_fields(self):
        before = len(self.application.state.task_repository.list_for_project("project-a"))
        for payload in (
            {"feedback": "   "},
            {"feedback": "x" * 4001},
            {"feedback": FEEDBACK, "api_key": "forbidden"},
        ):
            with self.subTest(payload=list(payload)):
                self.assertEqual(self.post_revise(payload).status_code, 422)
        after = len(self.application.state.task_repository.list_for_project("project-a"))
        self.assertEqual(after, before)

    def test_04_capability_preflight_creates_no_task(self):
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import CreativeActionService

        unavailable = CapabilityService(environment={}, which=lambda _name: None)
        self.application.state.capability_service = unavailable
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            unavailable,
            self.lock_manager,
        )
        before = len(self.application.state.task_repository.list_for_project("project-a"))
        self.assertEqual(self.post_revise().status_code, 503)
        self.assertEqual(self.post_regenerate().status_code, 503)
        after = len(self.application.state.task_repository.list_for_project("project-a"))
        self.assertEqual(after, before)

    def test_05_approved_and_not_started_states_are_rejected(self):
        for status in ("APPROVED", "NOT_STARTED"):
            with self.subTest(status=status):
                self.set_review_status(status)
                self.assertEqual(self.post_revise().status_code, 409)
                self.assertEqual(self.post_regenerate().status_code, 409)

    def test_06_active_task_returns_project_busy(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "b" * 32,
                project_id="project-a",
                operation=TaskOperation.CREATIVE_GENERATE,
                status=TaskStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
                correlation_id="req_busy_video_prompt",
            )
        )
        self.assertEqual(self.post_revise().status_code, 409)
        self.assertEqual(self.post_regenerate().status_code, 409)

    def test_07_worker_race_revalidates_before_provider(self):
        service = self.application.state.creative_action_service
        original = service._require_video_prompt_revise_allowed
        calls = 0

        def race(project_id: str):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.set_review_status("APPROVED")
            return original(project_id)

        with (
            patch.object(
                service,
                "_require_video_prompt_revise_allowed",
                side_effect=race,
            ),
            patch("storyboard._request_single_shot_visual_core") as provider,
        ):
            response = self.post_revise()
            self.assertEqual(response.status_code, 202)
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "ACTION_NOT_ALLOWED")
        provider.assert_not_called()

    def test_08_failed_later_shot_preserves_old_canonical_and_progress(self):
        from prompt_generator import PromptGenerationError

        canonical_before = (
            self.project_dir / "storyboard" / "video_prompts.json"
        ).read_bytes()
        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=["revised-one", PromptGenerationError("temporary")],
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(
            (self.project_dir / "storyboard" / "video_prompts.json").read_bytes(),
            canonical_before,
        )
        progress = json.loads(
            (
                self.project_dir
                / "storyboard"
                / "video_prompt_generation_progress.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(progress["shots"][0]["status"], "COMPLETED")
        self.assertEqual(progress["shots"][1]["status"], "FAILED")

    def test_09_openapi_examples_are_endpoint_specific(self):
        document = self.client.get("/openapi.json").json()
        for endpoint, operation in (
            ("revise", "VIDEO_PROMPT_REVISE"),
            ("regenerate", "VIDEO_PROMPT_REGENERATE"),
        ):
            example = document["paths"][
                f"/api/projects/{{project_id}}/planning/video-prompts/{endpoint}"
            ]["post"]["responses"]["202"]["content"]["application/json"]["example"]
            self.assertEqual(example["operation"], operation)


if __name__ == "__main__":
    unittest.main()
