from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import base_project, write_json, write_project
from tests.web.web_response_assertions import assert_public_payload


def provider_response(content: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def core_response(value: str) -> Mock:
    return provider_response(json.dumps({"visual_prompt_core": value}, ensure_ascii=False))


def creative_payload() -> dict:
    from storyboard import CreativeBrief

    return CreativeBrief(
        creative_concept="清新产品旅程",
        target_audience="年轻消费者",
        key_message="自然清爽",
        visual_direction="明亮棚拍",
        narrative_arc="产品特写到品牌定格",
        global_constraints={"must": [], "must_not": ["people"]},
    ).model_dump()


def storyboard_payload() -> dict:
    from storyboard import Storyboard, StoryboardShot, SubtitleCue, VoiceoverCue

    return Storyboard(
        total_duration=18,
        shots=[
            StoryboardShot(
                shot_id=shot_id,
                duration=6,
                purpose=f"purpose-{shot_id}",
                visual=f"visual-only-{shot_id}",
                camera=f"camera-{shot_id}",
                voiceover_cues=(
                    [VoiceoverCue(text="PRIVATE VOICE BODY", start_offset=2, end_offset=3)]
                    if shot_id == 1
                    else []
                ),
                subtitle_cues=(
                    [
                        SubtitleCue(
                            text="PRIVATE SUBTITLE BODY",
                            start_offset=3,
                            end_offset=4,
                            position="bottom_center",
                        )
                    ]
                    if shot_id == 1
                    else []
                ),
                video_constraints={
                    "reserve_subtitle_space": shot_id == 1,
                    "subtitle_safe_area": "bottom_center" if shot_id == 1 else "none",
                },
            )
            for shot_id in (1, 2, 3)
        ],
    ).model_dump()


class WebBackendPhase3C1VideoPromptGenerateTests(unittest.TestCase):
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
        self.project_dir = self.write_ready_project("project-a")
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

    def write_ready_project(self, project_id: str) -> Path:
        project = base_project(project_id=project_id, project_name="Video Prompt Test")
        project["request"].update(
            product_name="柠檬饮品",
            product_description="清爽饮品包装",
            user_notes="不要出现人物",
            duration_seconds=18,
            video_style="明亮商业广告",
            video_purpose="新品展示",
        )
        for stage, status in (
            ("CREATIVE", "COMPLETED"),
            ("CREATIVE_REVIEW", "APPROVED"),
            ("STORYBOARD", "COMPLETED"),
            ("STORYBOARD_REVIEW", "APPROVED"),
        ):
            project["stages"][stage]["status"] = status
        project["current_stage"] = "STORYBOARD_REVIEW"
        project["status"] = "APPROVED"
        directory = write_project(self.projects_root, project_id, project)
        write_json(directory / "concepts" / "creative_brief.json", creative_payload())
        write_json(directory / "storyboard" / "storyboard.json", storyboard_payload())
        return directory

    def post(self, project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/planning/video-prompts/generate",
            headers={"X-Correlation-ID": "req_phase3c1_video_prompt"},
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

    def read_project(self, directory: Path | None = None) -> dict:
        target = directory or self.project_dir
        return json.loads((target / "project.json").read_text(encoding="utf-8"))

    def test_01_accepted_operation_location_and_openapi_example(self) -> None:
        from storyboard import VideoPromptPlan

        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return VideoPromptPlan.model_validate(
                {
                    "shots": [
                        {
                            "shot_id": shot_id,
                            "visual_prompt_core": f"core-{shot_id}",
                            "video_prompt": f"final-{shot_id}",
                        }
                        for shot_id in (1, 2, 3)
                    ]
                }
            )

        with patch("video_prompt_workflow.generate_video_prompts_stage", side_effect=blocked):
            response = self.post()
            self.assertEqual(response.status_code, 202)
            payload = response.json()
            self.assertEqual(payload["operation"], "VIDEO_PROMPT_GENERATE")
            self.assertIn(payload["status"], {"QUEUED", "RUNNING"})
            self.assertEqual(response.headers["Location"], f"/api/tasks/{payload['task_id']}")
            self.assertTrue(entered.wait(timeout=1))
            workflow = self.client.get("/api/projects/project-a/workflow").json()
            self.assertNotIn("GENERATE_VIDEO_PROMPTS", workflow["available_actions"])
            release.set()
            self.wait_terminal(payload["task_id"])

        schema = self.client.get("/openapi.json").json()
        example = schema["paths"][
            "/api/projects/{project_id}/planning/video-prompts/generate"
        ]["post"]["responses"]["202"]["content"]["application/json"]["example"]
        self.assertEqual(example["operation"], "VIDEO_PROMPT_GENERATE")

    def test_02_real_core_per_shot_flow_is_minimal_locked_and_successful(self) -> None:
        import video_prompt_workflow
        from web_backend.locking import ProjectLockBusy

        original = video_prompt_workflow.generate_video_prompts_stage
        observed_lock: list[str] = []

        def checked(*args, **kwargs):
            def competing_writer() -> None:
                try:
                    with self.lock_manager.project_write("project-a"):
                        observed_lock.append("acquired")
                except ProjectLockBusy:
                    observed_lock.append("busy")

            competitor = Thread(target=competing_writer)
            competitor.start()
            competitor.join(timeout=1)
            return original(*args, **kwargs)

        with (
            patch(
                "video_prompt_workflow.generate_video_prompts_stage",
                side_effect=checked,
            ),
            patch(
                "prompt_generator.requests.post",
                side_effect=[core_response("core-one"), core_response("core-two"), core_response("core-three")],
            ) as provider,
            patch("video_generator.generate_video") as minimax,
            patch("voice_generation.generate_confirmed_voice") as tts,
            patch("subprocess.run") as ffmpeg,
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])

        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.resource_type, "VIDEO_PROMPTS")
        self.assertEqual(observed_lock, ["busy"])
        self.assertEqual(provider.call_count, 3)
        for index, call in enumerate(provider.call_args_list, start=1):
            text = call.kwargs["json"]["messages"][1]["content"]
            self.assertIn(f"visual-only-{index}", text)
            self.assertNotIn("PRIVATE VOICE BODY", text)
            self.assertNotIn("PRIVATE SUBTITLE BODY", text)
            self.assertNotIn('"shots"', text)
            for other in {1, 2, 3} - {index}:
                self.assertNotIn(f"visual-only-{other}", text)
        minimax.assert_not_called()
        tts.assert_not_called()
        ffmpeg.assert_not_called()

        canonical = json.loads(
            (self.project_dir / "storyboard" / "video_prompts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["shot_id"] for item in canonical["shots"]], [1, 2, 3])
        first = canonical["shots"][0]
        self.assertEqual(first["visual_prompt_core"], "core-one")
        markers = [
            "[Composition Constraint]",
            "[Global Hard Constraints]",
            "[Text Overlay Constraint]",
            "[Audio Constraint]",
        ]
        self.assertEqual(
            [first["video_prompt"].index(marker) for marker in markers],
            sorted(first["video_prompt"].index(marker) for marker in markers),
        )
        state = self.read_project()
        self.assertEqual(state["stages"]["VIDEO_PROMPT"]["status"], "COMPLETED")
        self.assertEqual(state["stages"]["PROMPT_REVIEW"]["status"], "WAITING_REVIEW")
        self.assertEqual(state["stages"]["VIDEO_GENERATION"]["status"], "NOT_STARTED")
        content = self.client.get(
            "/api/projects/project-a/planning/video-prompts"
        ).json()
        self.assertEqual(len(content["content"]["shots"]), 3)
        self.assertEqual(content["status"], "WAITING_REVIEW")
        public_task = self.client.get(f"/api/tasks/{task.task_id}").json()
        rendered_task = json.dumps(public_task, ensure_ascii=False).casefold()
        self.assertNotIn("core-one", rendered_task)
        self.assertNotIn("visual_prompt_core", rendered_task)
        self.assertNotIn("video_prompt", public_task.get("result", {}))
        assert_public_payload(self, public_task)

    def test_03_partial_failure_maps_safely_and_manual_resume_skips_successes(self) -> None:
        invalid = provider_response(
            '{"visual_prompt_core":"bad","visual_prompt_core":"duplicate"}'
        )
        with patch(
            "prompt_generator.requests.post",
            side_effect=[core_response("first-core"), core_response("second-core"), invalid, invalid, invalid],
        ) as first_provider:
            first_response = self.post()
            first_task = self.wait_terminal(first_response.json()["task_id"])
        self.assertEqual(first_provider.call_count, 5)
        self.assertEqual(first_task.status.value, "FAILED")
        self.assertEqual(first_task.error.code, "VIDEO_PROMPT_OUTPUT_INVALID")
        self.assertTrue(first_task.error.retryable)
        self.assertFalse((self.project_dir / "storyboard" / "video_prompts.json").exists())
        progress_path = self.project_dir / "storyboard" / "video_prompt_generation_progress.json"
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual([item["status"] for item in progress["shots"]], ["COMPLETED", "COMPLETED", "FAILED"])
        workflow = self.client.get("/api/projects/project-a/workflow").json()
        self.assertEqual(workflow["available_actions"], ["GENERATE_VIDEO_PROMPTS"])

        with patch(
            "prompt_generator.requests.post",
            side_effect=[core_response("third-core")],
        ) as resumed_provider:
            second_response = self.post()
            second_task = self.wait_terminal(second_response.json()["task_id"])
        self.assertEqual(resumed_provider.call_count, 1)
        self.assertEqual(second_task.status.value, "SUCCEEDED")
        history = self.client.get("/api/projects/project-a/tasks").json()["tasks"]
        self.assertEqual(len(history), 2)
        self.assertEqual([item["status"] for item in history], ["SUCCEEDED", "FAILED"])
        self.assertEqual(
            [item["operation"] for item in history],
            ["VIDEO_PROMPT_GENERATE", "VIDEO_PROMPT_GENERATE"],
        )

    def test_04_preflight_state_capability_busy_and_worker_race(self) -> None:
        from web_backend.services.capabilities import CapabilityService
        from web_backend.services.planning_actions import ActionNotAllowed, CreativeActionService

        data = self.read_project()
        data["stages"]["STORYBOARD_REVIEW"]["status"] = "WAITING_REVIEW"
        data["current_stage"] = "STORYBOARD_REVIEW"
        data["status"] = "WAITING_REVIEW"
        write_json(self.project_dir / "project.json", data)
        with patch("prompt_generator.requests.post") as provider:
            self.assertEqual(self.post().status_code, 409)
        provider.assert_not_called()

        write_json(self.project_dir / "project.json", base_project(project_id="project-a", project_name="reset"))
        # Restore the complete ready fixture without reusing mutated state.
        replacement = self.write_ready_project("project-a-restored")
        # Capability preflight uses the restored canonical project.
        unavailable = CapabilityService(environment={}, which=lambda _name: None)
        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            unavailable,
            self.lock_manager,
        )
        response = self.post("project-a-restored")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a-restored"),
            [],
        )

        self.application.state.creative_action_service = CreativeActionService(
            self.application.state.project_repository,
            self.application.state.task_service,
            self.capabilities,
            self.lock_manager,
        )
        service = self.application.state.creative_action_service
        original_gate = service._require_video_prompt_generate_allowed
        checks = 0

        def race_gate(project_id: str) -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                raise ActionNotAllowed("state changed")
            original_gate(project_id)

        with (
            patch.object(service, "_require_video_prompt_generate_allowed", side_effect=race_gate),
            patch("prompt_generator.requests.post") as provider,
        ):
            raced = self.post("project-a-restored")
            raced_task = self.wait_terminal(raced.json()["task_id"])
        self.assertEqual(raced_task.error.code, "ACTION_NOT_ALLOWED")
        provider.assert_not_called()
        self.assertFalse((replacement / "storyboard" / "video_prompts.json").exists())

    def test_05_busy_interrupted_and_progress_queries_never_replay_or_rewrite(self) -> None:
        from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus

        entered = Event()
        release = Event()

        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            raise RuntimeError("stop")

        with patch("video_prompt_workflow.generate_video_prompts_stage", side_effect=blocked):
            first = self.post()
            self.assertTrue(entered.wait(timeout=1))
            second = self.post()
            self.assertEqual(second.status_code, 409)
            self.assertEqual(second.json()["error"]["code"], "PROJECT_BUSY")
            release.set()
            self.wait_terminal(first.json()["task_id"])

        # A restarted backend marks an abandoned Web task interrupted without
        # replaying provider work. Business progress remains a separate file.
        data = self.read_project()
        data["stages"]["VIDEO_PROMPT"]["status"] = "RUNNING"
        data["current_stage"] = "VIDEO_PROMPT"
        data["status"] = "RUNNING"
        write_json(self.project_dir / "project.json", data)
        progress_path = self.project_dir / "storyboard" / "video_prompt_generation_progress.json"
        write_json(
            progress_path,
            {
                "video_prompt_schema_version": 2,
                "storyboard_fingerprint": "a" * 64,
                "status": "RUNNING",
                "updated_at": "2026-08-19T00:00:00+00:00",
                "shots": [],
            },
        )
        before_bytes = progress_path.read_bytes()
        before_mtime = progress_path.stat().st_mtime_ns
        abandoned = TaskRecord(
            task_id=f"task_{'a' * 32}",
            project_id="project-a",
            operation=TaskOperation.VIDEO_PROMPT_GENERATE,
            status=TaskStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            correlation_id="req_interrupted_video_prompt",
        )
        self.application.state.task_repository.create(abandoned)
        with patch("prompt_generator.requests.post") as provider:
            interrupted = self.application.state.task_service.recover_interrupted_tasks()
            self.client.get("/api/projects/project-a/workflow")
            self.client.get("/api/projects/project-a/planning/video-prompts")
            self.client.get("/api/projects/project-a/tasks")
        provider.assert_not_called()
        self.assertEqual(interrupted[0].status.value, "INTERRUPTED")
        self.assertEqual(progress_path.read_bytes(), before_bytes)
        self.assertEqual(progress_path.stat().st_mtime_ns, before_mtime)
        workflow = self.client.get("/api/projects/project-a/workflow").json()
        self.assertIn("GENERATE_VIDEO_PROMPTS", workflow["available_actions"])

    def test_06_existing_canonical_or_review_state_rejects_initial_generate(self) -> None:
        write_json(
            self.project_dir / "storyboard" / "video_prompts.json",
            {"shots": [{"shot_id": 1, "video_prompt": "existing"}]},
        )
        with patch("prompt_generator.requests.post") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 409)
        provider.assert_not_called()

        (self.project_dir / "storyboard" / "video_prompts.json").unlink()
        data = self.read_project()
        data["stages"]["VIDEO_PROMPT"]["status"] = "COMPLETED"
        data["stages"]["PROMPT_REVIEW"]["status"] = "WAITING_REVIEW"
        data["current_stage"] = "PROMPT_REVIEW"
        data["status"] = "WAITING_REVIEW"
        write_json(self.project_dir / "project.json", data)
        with patch("prompt_generator.requests.post") as provider:
            response = self.post()
        self.assertEqual(response.status_code, 409)
        provider.assert_not_called()

    def test_07_provider_failure_does_not_degrade_to_output_invalid(self) -> None:
        from prompt_generator import PromptGenerationError

        with patch(
            "video_prompt_workflow.generate_video_prompts_stage",
            side_effect=PromptGenerationError("private provider network detail"),
        ):
            response = self.post()
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "PROVIDER_REQUEST_FAILED")
        self.assertNotEqual(task.error.code, "VIDEO_PROMPT_OUTPUT_INVALID")
        self.assertNotIn("private", task.error.message.casefold())


if __name__ == "__main__":
    unittest.main()
