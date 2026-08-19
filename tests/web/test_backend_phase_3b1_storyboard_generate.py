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


def storyboard_result():
    from storyboard import Storyboard

    return Storyboard.model_validate(
        {
            "total_duration": 18,
            "shots": [
                {
                    "shot_id": index,
                    "duration": 6,
                    "purpose": "展示产品卖点",
                    "visual": "产品在明亮背景中展示",
                    "camera": "平稳推进",
                    "voiceover_cues": [],
                    "subtitle_cues": [],
                    "video_constraints": {
                        "reserve_subtitle_space": False,
                        "subtitle_safe_area": "none",
                    },
                }
                for index in range(1, 4)
            ],
        }
    )


def semantic_storyboard_payload() -> dict:
    first_voice = "LEE柠檬鲜切为光，清新果香绽放。"
    second_voice = "每一滴都唤醒明亮年轻活力。"
    return {
        "total_duration": 18,
        "shots": [
            {
                "shot_id": index,
                "duration": 6,
                "purpose": "展示产品卖点",
                "visual": "产品在明亮背景中展示",
                "camera": "平稳推进",
                "voiceover_cues": (
                    [{"text": first_voice, "placement": "middle"}]
                    if index == 1
                    else (
                        [{"text": second_voice, "placement": "middle"}]
                        if index == 2
                        else []
                    )
                ),
                "subtitle_cues": (
                    [
                        {
                            "text": "自然清爽",
                            "placement": "middle",
                            "position": "bottom_center",
                        }
                    ]
                    if index == 1
                    else []
                ),
                "video_constraints": {
                    "reserve_subtitle_space": index == 1,
                    "subtitle_safe_area": (
                        "bottom_center" if index == 1 else "none"
                    ),
                },
            }
            for index in range(1, 4)
        ],
    }


class WebBackendPhase3B1StoryboardGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        from storyboard import CreativeBrief
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
        self.project_a = self._write_approved_project("project-a", "第一项目")
        self.project_b = self._write_approved_project("project-b", "第二项目")
        brief_payload = creative_brief().model_dump()
        brief_payload["narration_plan"] = {
            "enabled": True,
            "tone": "清爽有活力",
            "full_script": (
                "LEE柠檬鲜切为光，清新果香层层绽放，"
                "每一滴都唤醒明亮年轻活力。"
            ),
            "target_duration_seconds": 8,
        }
        brief_payload["global_constraints"] = {
            "must": [],
            "must_not": ["people"],
        }
        brief_payload["av_timeline_constraints"] = {
            "forbidden_windows": [
                {
                    "start": 0,
                    "end": 2,
                    "tracks": ["voiceover", "subtitle"],
                }
            ]
        }
        self.brief = CreativeBrief.model_validate(brief_payload)
        for directory in (self.project_a, self.project_b):
            write_json(
                directory / "concepts" / "creative_brief.json",
                self.brief.model_dump(),
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

    def _write_approved_project(self, project_id: str, name: str) -> Path:
        project = base_project(project_id=project_id, project_name=name)
        project["request"]["user_notes"] = (
            "不要出现人物；前2秒不要出现旁白和字幕"
        )
        project["stages"]["CREATIVE"]["status"] = "COMPLETED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        project["current_stage"] = "CREATIVE_REVIEW"
        project["status"] = "APPROVED"
        return write_project(self.projects_root, project_id, project)

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/storyboard/generate",
            headers={"X-Correlation-ID": "req_phase3b1_storyboard"},
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

    def write_project_data(self, directory: Path, payload: dict) -> None:
        write_json(directory / "project.json", payload)

    def test_01_returns_202_queued_operation_and_location(self):
        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return storyboard_result()

        with patch(
            "storyboard_workflow.generate_storyboard_stage",
            side_effect=blocked,
        ):
            response = self.post()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"], "STORYBOARD_GENERATE")
            self.assertEqual(response.json()["status"], "QUEUED")
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
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_02_success_uses_core_scheduler_and_stops_at_review(self):
        with patch(
            "storyboard.deepseek_json_request",
            return_value=semantic_storyboard_payload(),
        ) as provider:
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        provider_prompt = provider.call_args.args[2]
        self.assertIn("people", provider_prompt)
        self.assertIn('"start": 0.0', provider_prompt)
        self.assertIn('"end": 2.0', provider_prompt)
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.resource_type, "STORYBOARD")
        self.assertEqual(task.result.resource_id, "project-a")
        self.assertIsNone(task.result.version)
        saved = json.loads(
            (self.project_a / "storyboard" / "storyboard.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["total_duration"], 18)
        self.assertEqual(len(saved["shots"]), 3)
        voiceover_cue = saved["shots"][0]["voiceover_cues"][0]
        subtitle_cue = saved["shots"][0]["subtitle_cues"][0]
        self.assertEqual(voiceover_cue["start_offset"], 2)
        self.assertGreater(
            voiceover_cue["end_offset"],
            voiceover_cue["start_offset"],
        )
        self.assertGreaterEqual(subtitle_cue["start_offset"], 2)
        self.assertGreater(subtitle_cue["end_offset"], subtitle_cue["start_offset"])
        self.assertTrue(
            saved["shots"][0]["video_constraints"]["reserve_subtitle_space"]
        )
        project = self.read_project(self.project_a)
        self.assertEqual(project["stages"]["STORYBOARD"]["status"], "COMPLETED")
        self.assertEqual(
            project["stages"]["STORYBOARD_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        self.assertEqual(project["current_stage"], "STORYBOARD_REVIEW")
        self.assertEqual(project["stages"]["VIDEO_PROMPT"]["status"], "NOT_STARTED")
        self.assertFalse(
            (self.project_a / "storyboard" / "video_prompts.json").exists()
        )

    def test_03_web_calls_shared_core_callable_once(self):
        from storyboard_workflow import generate_storyboard_stage

        with (
            patch(
                "storyboard_workflow.generate_storyboard_stage",
                wraps=generate_storyboard_stage,
            ) as shared,
            patch(
                "storyboard_workflow.generate_storyboard",
                return_value=storyboard_result(),
            ),
        ):
            response = self.post()
            self.wait_terminal(response.json()["task_id"])
        shared.assert_called_once()

    def test_04_unapproved_or_existing_storyboard_is_action_not_allowed(self):
        cases = (
            ("WAITING_REVIEW", "NOT_STARTED", "NOT_STARTED"),
            ("APPROVED", "COMPLETED", "WAITING_REVIEW"),
        )
        for review, storyboard, storyboard_review in cases:
            with self.subTest(review=review, storyboard=storyboard):
                project = self.read_project(self.project_a)
                project["stages"]["CREATIVE_REVIEW"]["status"] = review
                project["stages"]["STORYBOARD"]["status"] = storyboard
                project["stages"]["STORYBOARD_REVIEW"]["status"] = storyboard_review
                project["status"] = review if storyboard == "NOT_STARTED" else storyboard_review
                self.write_project_data(self.project_a, project)
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")
        self.assertFalse(self.runtime_root.exists())

    def test_05_deepseek_unavailable_creates_no_task(self):
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

    def test_06_active_task_returns_project_busy_without_second_task(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.CREATIVE_REVISE,
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_active",
            )
        )
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            1,
        )

    def test_07_worker_uses_project_lock_and_revalidates(self):
        service = self.application.state.creative_action_service
        with (
            patch.object(
                self.lock_manager,
                "project_write",
                wraps=self.lock_manager.project_write,
            ) as project_write,
            patch.object(
                service,
                "_require_storyboard_generate_allowed",
                wraps=service._require_storyboard_generate_allowed,
            ) as validator,
            patch(
                "storyboard_workflow.generate_storyboard_stage",
                return_value=storyboard_result(),
            ),
        ):
            response = self.post()
            self.wait_terminal(response.json()["task_id"])
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_08_worker_race_rejection_never_calls_provider(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with (
            patch.object(
                service,
                "_require_storyboard_generate_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("storyboard.generate_storyboard", provider),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(validator.call_count, 2)
        provider.assert_not_called()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "ACTION_NOT_ALLOWED")

    def test_09_provider_failure_preserves_old_canonical_and_is_safe(self):
        from prompt_generator import PromptGenerationError

        canonical = self.project_a / "storyboard" / "storyboard.json"
        old = {"legacy": "must remain"}
        write_json(canonical, old)
        before = canonical.read_bytes()
        with patch(
            "storyboard_workflow.generate_storyboard",
            side_effect=PromptGenerationError("private provider detail"),
        ) as provider:
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        provider.assert_called_once()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "PROVIDER_REQUEST_FAILED")
        self.assertNotIn("private", task.error.message)
        self.assertEqual(canonical.read_bytes(), before)

    def test_10_scheduler_and_validation_failures_are_distinct_and_no_retry(self):
        from storyboard import StoryboardError

        for error, expected in (
            (StoryboardError("SCHEDULE_UNSATISFIABLE: too long"), "SCHEDULE_UNSATISFIABLE"),
            (StoryboardError("invalid semantic output"), "STORYBOARD_OUTPUT_INVALID"),
        ):
            with self.subTest(expected=expected):
                project = self.read_project(self.project_a)
                project["stages"]["STORYBOARD"]["status"] = "NOT_STARTED"
                project["stages"]["STORYBOARD_REVIEW"]["status"] = "NOT_STARTED"
                project["current_stage"] = "CREATIVE_REVIEW"
                project["status"] = "APPROVED"
                project["last_error"] = None
                self.write_project_data(self.project_a, project)
                with patch(
                    "storyboard_workflow.generate_storyboard",
                    side_effect=error,
                ) as provider:
                    response = self.post()
                    task = self.wait_terminal(response.json()["task_id"])
                provider.assert_called_once()
                self.assertEqual(task.status.value, "FAILED")
                self.assertEqual(task.error.code, expected)

    def test_11_different_projects_can_generate_independently(self):
        release = Event()

        def blocked(*_args, **_kwargs):
            release.wait(timeout=2)
            return storyboard_result()

        with patch(
            "storyboard_workflow.generate_storyboard_stage",
            side_effect=blocked,
        ):
            first = self.post("project-a")
            second = self.post("project-b")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            release.set()
            first_task = self.wait_terminal(first.json()["task_id"])
            second_task = self.wait_terminal(second.json()["task_id"])
        self.assertEqual(first_task.status.value, "SUCCEEDED")
        self.assertEqual(second_task.status.value, "SUCCEEDED")

    def test_12_restart_interrupts_without_provider_replay(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        self.application.state.task_repository.create(
            TaskRecord(
                task_id="task_" + "c" * 32,
                project_id="project-a",
                operation=TaskOperation.STORYBOARD_GENERATE,
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_restart_storyboard",
            )
        )
        provider = Mock(side_effect=AssertionError("restart must not replay"))
        with patch("storyboard.generate_storyboard", provider):
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0].status.value, "INTERRUPTED")
        provider.assert_not_called()

    def test_13_project_errors_are_safe_and_create_no_task(self):
        for project_id, expected in (
            ("missing", "PROJECT_NOT_FOUND"),
            ("C:unsafe", "INVALID_PROJECT_ID"),
        ):
            with self.subTest(project_id=project_id):
                response = self.post(project_id)
                self.assertIn(response.status_code, {404, 422})
                self.assertEqual(response.json()["error"]["code"], expected)
        self.assertFalse(self.runtime_root.exists())

    def test_14_task_json_contains_only_small_result_reference(self):
        with patch(
            "storyboard_workflow.generate_storyboard",
            return_value=storyboard_result(),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        payload = json.loads(
            (
                self.runtime_root
                / "tasks"
                / f"{task.task_id}.json"
            ).read_text(encoding="utf-8")
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(
            payload["result"],
            {
                "resource_type": "STORYBOARD",
                "resource_id": "project-a",
                "version": None,
            },
        )
        for forbidden in ("shots", "voiceover_cues", "subtitle_cues", "mock-deepseek-key"):
            self.assertNotIn(forbidden, serialized)
        assert_public_payload(self, response.json())

    def test_15_no_network_media_or_downstream_stage_is_invoked(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("provider")),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch("video_generator.generate_video", side_effect=AssertionError("minimax")),
            patch("voice_generation.generate_confirmed_voice", side_effect=AssertionError("tts")),
            patch("storyboard.generate_video_prompts", side_effect=AssertionError("video prompt")),
            patch("storyboard_workflow.generate_storyboard", return_value=storyboard_result()),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_16_openapi_has_storyboard_operation_example(self):
        schema = self.client.get("/openapi.json").json()
        response = schema["paths"][
            "/api/projects/{project_id}/planning/storyboard/generate"
        ]["post"]["responses"]["202"]
        media_type = response["content"]["application/json"]
        self.assertEqual(
            media_type["schema"],
            {"$ref": "#/components/schemas/TaskRecord"},
        )
        self.assertEqual(
            media_type["example"]["operation"],
            "STORYBOARD_GENERATE",
        )


if __name__ == "__main__":
    unittest.main()
