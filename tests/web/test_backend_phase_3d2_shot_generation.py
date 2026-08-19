from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from video_provider import (
    DownloadResult,
    ProviderErrorCode,
    ProviderTask,
    ProviderTaskStatus,
    VideoProviderError,
)


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class WebBackendPhase3D2ShotGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.project_dir = self._write_project("project-a", "project-a")
        self._write_project("project-b", "project-b")
        self.asset_id = self._write_reference(self.project_dir)
        self.environment = patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "mock-deepseek-key",
                "MINIMAX_API_KEY": "mock-hailuo-key",
                "MINIMAX_H3_API_KEY": "mock-h3-key",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real provider/network call"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            )
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)
        self.submit_calls = 0
        self.poll_calls = 0
        self.download_calls = 0

    def _write_project(self, project_id: str, directory_name: str) -> Path:
        project = base_project(project_id=project_id, project_name="Shot 生成测试")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "NOT_STARTED",
                "generation_count": 0,
                "active_prompt_version": 2,
                "active_video_version": None,
                "approved_video_version": None,
                "pending_video_version": None,
                "prompt_versions": [
                    {
                        "shot_id": 1,
                        "version": 2,
                        "prompt": "approved active prompt",
                        "source": "ai_revision",
                        "safety_prompt": "safe reviewed prompt",
                        "safety_is_safe": True,
                        "safety_risk_notes": [],
                        "safety_checked_at": "2026-08-19T00:00:00",
                    }
                ],
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        directory = write_project(self.projects_root, directory_name, project)
        write_json(
            directory / "storyboard" / "storyboard.json",
            {
                "total_duration": 6,
                "shots": [
                    {
                        "shot_id": 1,
                        "duration": 6,
                        "purpose": "product closeup",
                        "visual": "product on table",
                        "camera": "static",
                        "voiceover_cues": [],
                        "subtitle_cues": [],
                        "video_constraints": {
                            "reserve_subtitle_space": False,
                            "subtitle_safe_area": "none",
                        },
                    }
                ],
            },
        )
        write_json(
            directory / "storyboard" / "video_prompts.json",
            {
                "shots": [
                    {
                        "shot_id": 1,
                        "visual_prompt_core": "approved core",
                        "video_prompt": "approved active prompt",
                    }
                ]
            },
        )
        return directory

    @staticmethod
    def _write_reference(directory: Path) -> str:
        target = directory / "references" / "project" / "ref_001.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_PNG)
        write_json(
            directory / "references" / "reference_manifest.json",
            {
                "version": 1,
                "assets": [
                    {
                        "asset_id": "ref_001",
                        "filename": target.name,
                        "type": "reference_image",
                        "source": "user_upload",
                        "project_path": "references/project/ref_001.png",
                        "sha256": hashlib.sha256(_PNG).hexdigest(),
                        "file_size": len(_PNG),
                        "width": 1,
                        "height": 1,
                    }
                ],
            },
        )
        return "ref_001"

    @staticmethod
    def payload(
        mode: str = "none",
        *,
        selection: str = "AUTO",
        model: str | None = None,
        assets: list[str] | None = None,
    ) -> dict:
        return {
            "model_selection": selection,
            "requested_model": model,
            "visual_input": {"mode": mode, "asset_ids": assets or []},
        }

    def preflight(self, payload: dict | None = None):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=payload or self.payload(),
        )

    def start(self, payload: dict | None = None, *, confirm: bool = True):
        config = payload or self.payload()
        checked = self.preflight(config)
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.json()["ready"])
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={
                **config,
                "preflight_fingerprint": checked.json()["preflight_fingerprint"],
                "confirm_paid_call": confirm,
            },
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

    def provider_patches(
        self,
        *,
        model: str = "hailuo",
        poll_error: Exception | None = None,
        submit_error: Exception | None = None,
        download_error: Exception | None = None,
        write_before_download_error: bool = False,
    ) -> ExitStack:
        from providers.minimax_h3_provider import MiniMaxH3Provider
        from providers.minimax_hailuo_provider import MiniMaxHailuoProvider

        provider = MiniMaxHailuoProvider if model == "hailuo" else MiniMaxH3Provider

        def submit(adapter, request, task_logger=None):
            del task_logger
            self.submit_calls += 1
            if submit_error:
                raise submit_error
            return ProviderTask(
                adapter.provider_name,
                adapter.model_name,
                adapter.api_version,
                adapter.generation_mode(request.required_capability),
                "provider-task-001",
            )

        def poll(adapter, task, task_logger=None):
            del adapter, task_logger
            self.poll_calls += 1
            generation = json.loads(
                (self.project_dir / "shots" / "shot_01" / "v001" / "generation.json").read_text(encoding="utf-8")
            )
            self.assertEqual(generation["provider_task_id"], "provider-task-001")
            if poll_error:
                raise poll_error
            return task.evolve(
                status=ProviderTaskStatus.COMPLETED,
                provider_file_id="provider-file-001" if model == "hailuo" else None,
                output_locator="https://provider.invalid/video" if model == "h3" else None,
            )

        def download(adapter, task, output_path, request, task_logger=None):
            del adapter, request, task_logger
            self.download_calls += 1
            generation = json.loads(
                (self.project_dir / "shots" / "shot_01" / "v001" / "generation.json").read_text(encoding="utf-8")
            )
            if model == "hailuo":
                self.assertEqual(generation["file_id"], "provider-file-001")
            if download_error:
                if write_before_download_error:
                    output_path.write_bytes(b"complete-video")
                raise download_error
            output_path.write_bytes(b"mock-mp4")
            return DownloadResult(output_path, len(b"mock-mp4"))

        stack = ExitStack()
        stack.enter_context(patch.object(provider, "submit", autospec=True, side_effect=submit))
        stack.enter_context(patch.object(provider, "poll", autospec=True, side_effect=poll))
        stack.enter_context(patch.object(provider, "download", autospec=True, side_effect=download))
        return stack

    def test_01_confirmation_and_stale_guards_create_no_task_or_provider_call(self):
        checked = self.preflight().json()
        missing = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={**self.payload(), "preflight_fingerprint": checked["preflight_fingerprint"]},
        )
        self.assertEqual(missing.status_code, 422)
        denied = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={
                **self.payload(),
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": False,
            },
        )
        self.assertEqual(denied.status_code, 422)
        self.assertEqual(denied.json()["error"]["code"], "PAID_CALL_CONFIRMATION_REQUIRED")
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        project["video_generation"]["shots"]["1"]["prompt_versions"][0]["prompt"] = "changed prompt"
        write_json(self.project_dir / "project.json", project)
        stale = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={
                **self.payload(),
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": True,
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "GENERATION_PREFLIGHT_STALE")
        self.assertEqual(self.submit_calls, 0)
        self.assertFalse(self.runtime_root.exists())

    def test_02_asset_identity_change_makes_reference_preflight_stale(self):
        config = self.payload("reference_asset", assets=[self.asset_id])
        checked = self.preflight(config).json()
        manifest_path = self.project_dir / "references" / "reference_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["source"] = "system_generated"
        write_json(manifest_path, manifest)
        response = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={
                **config,
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": True,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.runtime_root.exists())

    def test_03_success_submits_once_persists_order_and_complete_v001_bundle(self):
        with self.provider_patches():
            response = self.start()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["operation"], "SHOT_GENERATE")
            self.assertEqual(response.json()["target_id"], "shot_01")
            self.assertEqual(response.headers["Location"], f"/api/tasks/{response.json()['task_id']}")
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.resource_type, "SHOT_VIDEO")
        self.assertEqual(task.result.resource_id, "shot_01")
        self.assertEqual(task.result.version, 1)
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), (1, 1, 1))
        bundle = self.project_dir / "shots" / "shot_01" / "v001"
        self.assertEqual(
            {path.name for path in bundle.iterdir()},
            {"video.mp4", "prompt.json", "safety.json", "generation.json", "review.json"},
        )
        generation = json.loads((bundle / "generation.json").read_text(encoding="utf-8"))
        prompt = json.loads((bundle / "prompt.json").read_text(encoding="utf-8"))
        review = json.loads((bundle / "review.json").read_text(encoding="utf-8"))
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        shot = project["video_generation"]["shots"]["1"]
        self.assertEqual(generation["provider_model"], "MiniMax-Hailuo-2.3")
        self.assertEqual(generation["provider_api_version"], "v1")
        self.assertEqual(generation["generation_mode"], "text_to_video")
        self.assertEqual(generation["duration"], 6)
        self.assertEqual(generation["resolution"], "768P")
        self.assertEqual(generation["visual_input"]["mode"], "none")
        self.assertEqual(prompt["prompt_version"], 2)
        self.assertEqual(review["review_result"], "WAITING_REVIEW")
        self.assertEqual(shot["generation_count"], 1)
        self.assertEqual(shot["status"], "WAITING_REVIEW")
        self.assertEqual(shot["active_video_version"], 1)
        self.assertIsNone(shot["approved_video_version"])
        public = task.model_dump_json()
        self.assertNotIn("provider-task", public)
        self.assertNotIn("provider-file", public)

    def test_04_auto_routing_covers_first_frame_and_reference_without_downgrade(self):
        first = self.payload("first_frame", assets=[self.asset_id])
        with self.provider_patches():
            first_response = self.start(first)
            first_task = self.wait_terminal(first_response.json()["task_id"])
        self.assertEqual(first_task.status.value, "SUCCEEDED")
        first_generation = json.loads((self.project_dir / "shots" / "shot_01" / "v001" / "generation.json").read_text(encoding="utf-8"))
        self.assertEqual(first_generation["generation_mode"], "first_frame")

        shutil.rmtree(self.project_dir)
        self.project_dir = self._write_project("project-a", "project-a")
        self.asset_id = self._write_reference(self.project_dir)
        self.submit_calls = self.poll_calls = self.download_calls = 0
        reference = self.payload("reference_asset", assets=[self.asset_id])
        with self.provider_patches(model="h3"):
            h3_response = self.start(reference)
            h3_task = self.wait_terminal(h3_response.json()["task_id"])
        self.assertEqual(h3_task.status.value, "SUCCEEDED")
        h3_generation = json.loads((self.project_dir / "shots" / "shot_01" / "v001" / "generation.json").read_text(encoding="utf-8"))
        self.assertEqual(h3_generation["provider_model"], "MiniMax-H3")
        self.assertEqual(h3_generation["generation_mode"], "reference_generation")

    def test_05_poll_failure_preserves_task_id_and_manual_resume_never_submits_again(self):
        failure = VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            "poll failed",
            retryable=True,
        )
        with self.provider_patches(poll_error=failure):
            response = self.start()
            failed = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(failed.status.value, "FAILED")
        self.assertEqual(self.submit_calls, 1)
        status = self.client.get("/api/projects/project-a/shots/shot_01/generation/status")
        self.assertEqual(status.json()["resume_kind"], "POLL_EXISTING_TASK")
        with self.provider_patches():
            resumed = self.client.post("/api/projects/project-a/shots/shot_01/generation/resume")
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status.value, "SUCCEEDED")
        self.assertEqual(terminal.result.version, 1)
        self.assertEqual(self.submit_calls, 1)
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(project["video_generation"]["shots"]["1"]["generation_count"], 1)

    def test_06_download_failure_preserves_file_id_and_resume_skips_submit_and_poll(self):
        failure = VideoProviderError(
            ProviderErrorCode.DOWNLOAD_FAILED,
            "download failed",
            retryable=True,
        )
        with self.provider_patches(download_error=failure):
            response = self.start()
            failed = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(failed.status.value, "FAILED")
        status = self.client.get("/api/projects/project-a/shots/shot_01/generation/status").json()
        self.assertEqual(status["resume_kind"], "DOWNLOAD_EXISTING_FILE")
        before_submit, before_poll = self.submit_calls, self.poll_calls
        with self.provider_patches():
            resumed = self.client.post("/api/projects/project-a/shots/shot_01/generation/resume")
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status.value, "SUCCEEDED")
        self.assertEqual(terminal.result.version, 1)
        self.assertEqual(self.submit_calls, before_submit)
        self.assertEqual(self.poll_calls, before_poll)

    def test_07_local_video_finalization_resume_uses_no_provider_method(self):
        failure = OSError("local finalize interruption")

        from providers.minimax_hailuo_provider import MiniMaxHailuoProvider

        with self.provider_patches(
            download_error=failure,
            write_before_download_error=True,
        ):
            response = self.start()
            self.wait_terminal(response.json()["task_id"])
        status = self.client.get("/api/projects/project-a/shots/shot_01/generation/status").json()
        self.assertEqual(status["resume_kind"], "FINALIZE_LOCAL_VIDEO")
        with patch.object(MiniMaxHailuoProvider, "submit", side_effect=AssertionError("submit")), patch.object(MiniMaxHailuoProvider, "poll", side_effect=AssertionError("poll")), patch.object(MiniMaxHailuoProvider, "download", side_effect=AssertionError("download")):
            resumed = self.client.post("/api/projects/project-a/shots/shot_01/generation/resume")
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status.value, "SUCCEEDED")
        self.assertEqual(terminal.result.version, 1)

    def test_08_ambiguous_submit_is_not_resumable_and_never_auto_retries(self):
        ambiguous = VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            "timeout",
            retryable=True,
        )
        with self.provider_patches(submit_error=ambiguous):
            response = self.start()
            failed = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(failed.status.value, "FAILED")
        self.assertEqual(failed.error.code, "SUBMISSION_UNKNOWN")
        self.assertEqual(self.submit_calls, 1)
        status = self.client.get("/api/projects/project-a/shots/shot_01/generation/status").json()
        self.assertEqual(status["state"], "SUBMISSION_UNKNOWN")
        self.assertFalse(status["resume_available"])
        self.assertFalse(status["provider_submission_known"])
        resume = self.client.post("/api/projects/project-a/shots/shot_01/generation/resume")
        self.assertEqual(resume.status_code, 409)
        self.assertEqual(self.submit_calls, 1)

    def test_09_worker_revalidation_catches_queued_state_change_without_provider(self):
        from web_backend.models.tasks import TaskOperation, TaskResultReference

        entered = Event()
        release = Event()

        def blocker():
            entered.set()
            release.wait(timeout=2)
            return TaskResultReference(resource_type="TEST", resource_id="project-b")

        blocker_task = self.application.state.task_service.submit(
            project_id="project-b",
            operation=TaskOperation.CREATIVE_GENERATE,
            correlation_id="req_blocker",
            callable_=blocker,
        )
        self.assertTrue(entered.wait(timeout=1))
        checked = self.preflight().json()
        response = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/start",
            json={
                **self.payload(),
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": True,
            },
        )
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        project["video_generation"]["shots"]["1"]["prompt_versions"][0]["prompt"] = "changed while queued"
        write_json(self.project_dir / "project.json", project)
        release.set()
        self.wait_terminal(blocker_task.task_id)
        terminal = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(terminal.status.value, "FAILED")
        self.assertEqual(terminal.error.code, "ACTION_NOT_ALLOWED")
        self.assertEqual(self.submit_calls, 0)

    def test_10_status_get_is_zero_write_and_never_exposes_provider_identifiers(self):
        before = tree_snapshot(self.project_dir)
        response = self.client.get("/api/projects/project-a/shots/shot_01/generation/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "NOT_STARTED")
        self.assertEqual(tree_snapshot(self.project_dir), before)
        rendered = json.dumps(response.json()).lower()
        self.assertNotIn("provider_task_id", rendered)
        self.assertNotIn("file_id", rendered)
        self.assertNotIn("path", rendered)

        from web_backend.models.tasks import TaskOperation, TaskResultReference

        entered = Event()
        release = Event()

        def other_shot_task():
            entered.set()
            release.wait(timeout=2)
            return TaskResultReference(resource_type="SHOT_VIDEO", resource_id="shot_02")

        task = self.application.state.task_service.submit(
            project_id="project-a",
            operation=TaskOperation.SHOT_GENERATE,
            target_id="shot_02",
            correlation_id="req_other_shot",
            callable_=other_shot_task,
        )
        self.assertTrue(entered.wait(timeout=1))
        response = self.client.get("/api/projects/project-a/shots/shot_01/generation/status")
        self.assertEqual(response.json()["state"], "NOT_STARTED")
        self.assertEqual(tree_snapshot(self.project_dir), before)
        release.set()
        self.wait_terminal(task.task_id)

    def test_11_waiting_review_rejects_start_and_resume_without_new_provider(self):
        with self.provider_patches():
            response = self.start()
            self.wait_terminal(response.json()["task_id"])
        starts = self.submit_calls
        checked = self.preflight().json()
        self.assertFalse(checked["ready"])
        self.assertIn("SHOT_ALREADY_GENERATED", [item["code"] for item in checked["issues"]])
        resume = self.client.post("/api/projects/project-a/shots/shot_01/generation/resume")
        self.assertEqual(resume.status_code, 409)
        self.assertEqual(self.submit_calls, starts)

    def test_12_openapi_examples_are_operation_specific_and_public_safe(self):
        schema = self.client.get("/openapi.json").json()
        base = "/api/projects/{project_id}/shots/{shot_id}/generation"
        start = schema["paths"][f"{base}/start"]["post"]["responses"]["202"]["content"]["application/json"]["example"]
        resume = schema["paths"][f"{base}/resume"]["post"]["responses"]["202"]["content"]["application/json"]["example"]
        self.assertEqual(start["operation"], "SHOT_GENERATE")
        self.assertEqual(start["target_id"], "shot_01")
        self.assertEqual(resume["operation"], "SHOT_RESUME")
        self.assertEqual(resume["target_id"], "shot_01")
        rendered = json.dumps(schema, ensure_ascii=False).lower()
        for forbidden in ("provider_task_id", "file_id", "minimax_api_key", "d:\\"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
