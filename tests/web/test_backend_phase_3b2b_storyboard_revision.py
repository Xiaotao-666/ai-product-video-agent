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
from tests.web.test_backend_phase_3b1_storyboard_generate import (
    semantic_storyboard_payload,
    storyboard_result,
)
from tests.web.web_response_assertions import assert_public_payload


FEEDBACK = (
    "保留3个镜头；第二镜头减少旁白；第三镜头增加产品近景；"
    "前2秒继续不出现旁白和字幕。"
)
OLD_DIRECTION = "OLD_STORYBOARD_DIRECTION"


def replacement_payload() -> dict:
    payload = semantic_storyboard_payload()
    payload["shots"][0]["visual"] = "新鲜柠檬与产品在明亮背景中展示"
    payload["shots"][2]["visual"] = "产品包装微距特写"
    return payload


class WebBackendPhase3B2BStoryboardRevisionTests(unittest.TestCase):
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
        self.project_a = self._write_waiting_project("project-a", "第一项目")
        self.project_b = self._write_waiting_project("project-b", "第二项目")

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
            old = storyboard_result().model_dump()
            old["shots"][0]["visual"] = OLD_DIRECTION
            write_json(directory / "storyboard" / "storyboard.json", old)

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
        project["request"]["user_notes"] = (
            "不要出现人物；前2秒不要出现旁白和字幕"
        )
        project["stages"]["CREATIVE"]["status"] = "COMPLETED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        project["stages"]["STORYBOARD"]["status"] = "COMPLETED"
        project["stages"]["STORYBOARD_REVIEW"]["status"] = "WAITING_REVIEW"
        project["current_stage"] = "STORYBOARD_REVIEW"
        project["status"] = "WAITING_REVIEW"
        return write_project(self.projects_root, project_id, project)

    def post_revise(self, project_id: str = "project-a", payload=None):
        return self.client.post(
            f"/api/projects/{project_id}/planning/storyboard/revise",
            json={"feedback": FEEDBACK} if payload is None else payload,
            headers={"X-Correlation-ID": "req_phase3b2b_revise"},
        )

    def post_regenerate(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/storyboard/regenerate",
            headers={"X-Correlation-ID": "req_phase3b2b_regenerate"},
        )

    def wait_terminal(self, task_id: str, timeout: float = 4.0):
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

    def set_storyboard_review(self, status: str) -> None:
        project = self.read_project(self.project_a)
        if status == "NOT_STARTED":
            project["stages"]["STORYBOARD"]["status"] = "NOT_STARTED"
            project["current_stage"] = "STORYBOARD"
        else:
            project["stages"]["STORYBOARD"]["status"] = "COMPLETED"
            project["current_stage"] = "STORYBOARD_REVIEW"
        project["stages"]["STORYBOARD_REVIEW"]["status"] = status
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
            return storyboard_result()

        with patch("storyboard_workflow.revise_storyboard_stage", side_effect=blocked):
            response = self.post_revise()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"], "STORYBOARD_REVISE")
            self.assertEqual(response.json()["status"], "QUEUED")
            self.assertEqual(
                response.headers["Location"],
                f"/api/tasks/{response.json()['task_id']}",
            )
            self.assertTrue(entered.wait(timeout=1))
            release.set()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_02_regenerate_returns_202_without_body_and_distinct_operation(self):
        with patch(
            "storyboard_workflow.regenerate_storyboard_stage",
            return_value=storyboard_result(),
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "STORYBOARD_REGENERATE")
        self.assertEqual(task.status.value, "SUCCEEDED")

    def test_03_revise_uses_current_feedback_constraints_and_core_scheduler(self):
        from storyboard import schedule_av_timeline

        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=replacement_payload(),
            ) as provider,
            patch(
                "storyboard.schedule_av_timeline",
                wraps=schedule_av_timeline,
            ) as scheduler,
        ):
            response = self.post_revise(payload={"feedback": f"  {FEEDBACK}  "})
            task = self.wait_terminal(response.json()["task_id"])

        self.assertEqual(task.status.value, "SUCCEEDED")
        provider.assert_called_once()
        scheduler.assert_called_once()
        prompt = provider.call_args.args[2]
        self.assertIn(FEEDBACK, prompt)
        self.assertIn(OLD_DIRECTION, prompt)
        self.assertIn("前2秒不要出现旁白和字幕", prompt)
        self.assertIn("people", prompt)

        saved = json.loads(
            (self.project_a / "storyboard" / "storyboard.json").read_text(
                encoding="utf-8"
            )
        )
        first_voice = saved["shots"][0]["voiceover_cues"][0]
        first_subtitle = saved["shots"][0]["subtitle_cues"][0]
        self.assertGreaterEqual(first_voice["start_offset"], 2)
        self.assertGreater(first_voice["end_offset"], first_voice["start_offset"])
        self.assertGreaterEqual(first_subtitle["start_offset"], 2)
        self.assertGreater(first_subtitle["end_offset"], first_subtitle["start_offset"])
        self.assertEqual(saved["shots"][2]["visual"], "产品包装微距特写")
        self.assertTrue(saved["shots"][0]["video_constraints"]["reserve_subtitle_space"])
        project = self.read_project(self.project_a)
        self.assertEqual(project["stages"]["STORYBOARD"]["status"], "COMPLETED")
        self.assertEqual(
            project["stages"]["STORYBOARD_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        self.assertEqual(project["stages"]["VIDEO_PROMPT"]["status"], "NOT_STARTED")
        self.assertFalse((self.project_a / "prompts" / "video_prompts.json").exists())

    def test_04_regenerate_is_clean_and_reschedules_without_feedback(self):
        from storyboard import schedule_av_timeline

        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=replacement_payload(),
            ) as provider,
            patch(
                "storyboard.schedule_av_timeline",
                wraps=schedule_av_timeline,
            ) as scheduler,
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")
        provider.assert_called_once()
        scheduler.assert_called_once()
        prompt = provider.call_args.args[2]
        self.assertNotIn(OLD_DIRECTION, prompt)
        self.assertNotIn(FEEDBACK, prompt)
        self.assertIn("前2秒不要出现旁白和字幕", prompt)
        saved = json.loads(
            (self.project_a / "storyboard" / "storyboard.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["shots"][2]["visual"], "产品包装微距特写")
        self.assertGreaterEqual(
            saved["shots"][0]["voiceover_cues"][0]["start_offset"],
            2,
        )
        self.assertEqual(
            self.read_project(self.project_a)["stages"]["STORYBOARD_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        self.assertFalse((self.project_a / "prompts" / "video_prompts.json").exists())

    def test_05_feedback_validation_is_bounded_and_forbids_extra_fields(self):
        for payload in (
            {"feedback": ""},
            {"feedback": "   "},
            {"feedback": "x" * 4001},
            {"feedback": FEEDBACK, "api_key": "secret"},
            {"feedback": FEEDBACK, "path": "private"},
        ):
            with self.subTest(payload=list(payload)):
                response = self.post_revise(payload=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")
                self.assertNotIn("secret", response.text)
        self.assertFalse(self.runtime_root.exists())

    def test_06_feedback_and_storyboard_are_absent_from_task_json(self):
        with patch(
            "storyboard_workflow.revise_storyboard_stage",
            return_value=storyboard_result(),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        persisted = (
            self.runtime_root / "tasks" / f"{task.task_id}.json"
        ).read_text(encoding="utf-8")
        for payload in (response.text, task.model_dump_json(), persisted):
            self.assertNotIn(FEEDBACK, payload)
            self.assertNotIn(OLD_DIRECTION, payload)
            self.assertNotIn("shots", payload)
            self.assertNotIn("mock-deepseek-key", payload)
        self.assertEqual(task.result.resource_type, "STORYBOARD")
        self.assertEqual(task.result.resource_id, "project-a")
        self.assertIsNone(task.result.version)

    def test_07_provider_or_scheduler_failure_preserves_old_storyboard(self):
        from prompt_generator import PromptGenerationError
        from storyboard import StoryboardError

        cases = (
            (
                self.post_revise,
                "storyboard.deepseek_json_request",
                PromptGenerationError("private provider failure"),
                "PROVIDER_REQUEST_FAILED",
            ),
            (
                self.post_regenerate,
                "storyboard.schedule_av_timeline",
                StoryboardError("SCHEDULE_UNSATISFIABLE"),
                "SCHEDULE_UNSATISFIABLE",
            ),
        )
        for endpoint, target, error, code in cases:
            with self.subTest(endpoint=endpoint.__name__):
                before = (
                    self.project_a / "storyboard" / "storyboard.json"
                ).read_bytes()
                failing = Mock(side_effect=error)
                context = (
                    patch(target, failing)
                    if "deepseek" in target
                    else (
                        patch(
                            "storyboard.deepseek_json_request",
                            return_value=replacement_payload(),
                        ),
                        patch(target, failing),
                    )
                )
                if isinstance(context, tuple):
                    with context[0], context[1]:
                        response = endpoint()
                        task = self.wait_terminal(response.json()["task_id"])
                else:
                    with context:
                        response = endpoint()
                        task = self.wait_terminal(response.json()["task_id"])
                self.assertEqual(task.status.value, "FAILED")
                self.assertEqual(task.error.code, code)
                self.assertEqual(
                    (self.project_a / "storyboard" / "storyboard.json").read_bytes(),
                    before,
                )
                self.assertEqual(
                    self.read_project(self.project_a)["stages"]["STORYBOARD_REVIEW"]["status"],
                    "WAITING_REVIEW",
                )

    def test_08_not_started_or_approved_is_action_not_allowed_without_task(self):
        for status in ("NOT_STARTED", "APPROVED"):
            with self.subTest(status=status):
                self.set_storyboard_review(status)
                revise = self.post_revise()
                regenerate = self.post_regenerate()
                self.assertEqual(revise.status_code, 409)
                self.assertEqual(regenerate.status_code, 409)
                self.assertEqual(revise.json()["error"]["code"], "ACTION_NOT_ALLOWED")
                self.assertEqual(
                    regenerate.json()["error"]["code"],
                    "ACTION_NOT_ALLOWED",
                )
                self.assertFalse(self.runtime_root.exists())

    def test_09_capability_unavailable_creates_no_task(self):
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

    def test_10_active_task_returns_project_busy(self):
        self.create_active_task()
        for response in (self.post_revise(), self.post_regenerate()):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            1,
        )

    def test_11_different_projects_run_independently(self):
        release = Event()

        def blocked(*_args, **_kwargs):
            release.wait(timeout=2)
            return storyboard_result()

        with patch("storyboard_workflow.revise_storyboard_stage", side_effect=blocked):
            first = self.post_revise("project-a")
            second = self.post_revise("project-b")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            release.set()
            first_task = self.wait_terminal(first.json()["task_id"])
            second_task = self.wait_terminal(second.json()["task_id"])
        self.assertEqual(first_task.status.value, "SUCCEEDED")
        self.assertEqual(second_task.status.value, "SUCCEEDED")

    def test_12_worker_uses_project_lock_and_revalidates_state(self):
        service = self.application.state.creative_action_service
        with (
            patch.object(
                self.lock_manager,
                "project_write",
                wraps=self.lock_manager.project_write,
            ) as project_write,
            patch.object(
                service,
                "_require_storyboard_revise_allowed",
                wraps=service._require_storyboard_revise_allowed,
            ) as validator,
            patch(
                "storyboard_workflow.revise_storyboard_stage",
                return_value=storyboard_result(),
            ),
        ):
            response = self.post_revise()
            self.wait_terminal(response.json()["task_id"])
        project_write.assert_called_once_with("project-a")
        self.assertEqual(validator.call_count, 2)

    def test_13_worker_race_rejection_never_calls_provider(self):
        from web_backend.services.planning_actions import ActionNotAllowed

        service = self.application.state.creative_action_service
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with (
            patch.object(
                service,
                "_require_storyboard_regenerate_allowed",
                side_effect=[None, ActionNotAllowed("race")],
            ) as validator,
            patch("storyboard.deepseek_json_request", provider),
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(validator.call_count, 2)
        provider.assert_not_called()
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "ACTION_NOT_ALLOWED")

    def test_14_restart_interrupts_both_operations_without_replay(self):
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        now = datetime.now(timezone.utc)
        for suffix, project_id, operation in (
            ("c", "project-a", TaskOperation.STORYBOARD_REVISE),
            ("d", "project-b", TaskOperation.STORYBOARD_REGENERATE),
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
            patch("storyboard_workflow.revise_storyboard_stage", revise),
            patch("storyboard_workflow.regenerate_storyboard_stage", regenerate),
        ):
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
        self.assertEqual({item.status.value for item in interrupted}, {"INTERRUPTED"})
        self.assertEqual(len(interrupted), 2)
        revise.assert_not_called()
        regenerate.assert_not_called()

    def test_15_web_calls_each_shared_core_once_and_runner_does_not_retry(self):
        from prompt_generator import PromptGenerationError

        provider = Mock(side_effect=PromptGenerationError("private provider error"))
        with patch("storyboard.deepseek_json_request", provider):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        provider.assert_called_once()

        with (
            patch(
                "storyboard_workflow.regenerate_storyboard_stage",
                return_value=storyboard_result(),
            ) as shared,
        ):
            response = self.post_regenerate()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")
        shared.assert_called_once()
        self.assertNotIn("feedback", shared.call_args.kwargs)

    def test_16_openapi_uses_endpoint_specific_storyboard_examples(self):
        schema = self.client.get("/openapi.json").json()
        expected = {
            "/api/projects/{project_id}/planning/storyboard/generate": (
                "STORYBOARD_GENERATE"
            ),
            "/api/projects/{project_id}/planning/storyboard/revise": (
                "STORYBOARD_REVISE"
            ),
            "/api/projects/{project_id}/planning/storyboard/regenerate": (
                "STORYBOARD_REGENERATE"
            ),
        }
        for path, operation in expected.items():
            media = schema["paths"][path]["post"]["responses"]["202"]["content"][
                "application/json"
            ]
            self.assertEqual(media["schema"], {"$ref": "#/components/schemas/TaskRecord"})
            self.assertEqual(media["example"]["operation"], operation)

    def test_17_no_real_network_media_or_downstream_stage_is_invoked(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider network"),
            ),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch("video_generator.generate_video", side_effect=AssertionError("minimax")),
            patch(
                "voice_generation.generate_confirmed_voice",
                side_effect=AssertionError("tts"),
            ),
            patch(
                "storyboard.generate_video_prompts",
                side_effect=AssertionError("video prompt"),
            ),
            patch(
                "storyboard.deepseek_json_request",
                return_value=replacement_payload(),
            ),
        ):
            response = self.post_revise()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")
        assert_public_payload(self, response.json())

    def test_18_project_errors_are_safe_and_create_no_task(self):
        for project_id, expected in (
            ("missing", "PROJECT_NOT_FOUND"),
            ("C:unsafe", "INVALID_PROJECT_ID"),
        ):
            with self.subTest(project_id=project_id):
                response = self.post_revise(project_id)
                self.assertIn(response.status_code, {404, 422})
                self.assertEqual(response.json()["error"]["code"], expected)
        self.assertFalse(self.runtime_root.exists())


if __name__ == "__main__":
    unittest.main()
