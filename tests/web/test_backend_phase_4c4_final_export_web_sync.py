from __future__ import annotations

import json
import socket
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from export_assets import ExportAssetManager
from export_pipeline import ExportPipeline
from music_assets import MusicAssetManager
from music_generation import add_local_music
from music_mix import MusicMixSettingsManager
from music_provider_registry import build_music_provider_registry
from post_production import PostProductionPipeline, ProjectCompletionStatus
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from subtitle_assets import SubtitleAssetManager
from subtitle_generation import generate_subtitle_from_active_voice
from subtitle_provider_registry import build_subtitle_provider_registry
from tests.test_export_pipeline import FakeMediaRunner, LocalVoiceProvider, silent_wav
from tests.web.web_response_assertions import assert_public_payload
from voice_assets import VoiceAssetManager
from voice_provider import VoiceGenerationRequest
from web_backend.models.tasks import TaskError, TaskOperation, TaskRecord, TaskStatus


class WebBackendPhase4C4FinalExportWebSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.paths = create_project_paths(self.projects_root / "export-project")
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Final Export Web",
            {
                "product_name": "SSS sandwich",
                "product_description": "local final export",
                "user_notes": "",
                "duration_seconds": 2,
            },
        )
        checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "total_duration": 2.0,
            }
        )
        checkpoint.data["completion_status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        checkpoint.save()
        self.project_id = str(checkpoint.data["project_id"])
        self.paths.final_video_path().write_bytes(b"mock-assembly-video")
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {"narration_plan": {"enabled": True}},
        )
        self.add_voice()
        self.add_subtitle()
        self.add_music()

        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.services.final_export import FinalExportWebService
        from web_backend.settings import BackendSettings

        self.lock_manager = ProjectLockManager()
        self.app = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.root / "runtime",
                task_workers=1,
            ),
            lock_manager=self.lock_manager,
        )
        self.runner = FakeMediaRunner()
        self.app.state.final_export_web_service = FinalExportWebService(
            self.app.state.project_repository,
            self.app.state.task_service,
            self.lock_manager,
            pipeline_factory=lambda paths, state: ExportPipeline(
                paths,
                state,
                runner=self.runner,
                which=lambda name: name,
            ),
            token_key=b"phase-4c4-test-key" * 2,
        )
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.task_runner.shutdown)

    @property
    def base(self) -> str:
        return f"/api/projects/{self.project_id}/export"

    def checkpoint(self) -> ProjectCheckpoint:
        return ProjectCheckpoint.load(self.paths)

    def add_voice(self, *, script: str = "A fresh sandwich.") -> dict:
        return VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(script=script, voice="local", language="zh-CN"),
            LocalVoiceProvider(),
        )

    def add_subtitle(self) -> dict:
        return generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )

    def add_music(self) -> dict:
        source = self.root / f"music-{time.time_ns()}.wav"
        source.write_bytes(silent_wav())
        return add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            source,
        )

    def preflight(self):
        return self.client.post(f"{self.base}/preflight")

    def execute(self, preflight: dict | None = None, *, confirm: bool = True):
        prepared = preflight or self.preflight().json()
        return self.client.post(
            f"{self.base}/execute",
            json={
                "confirmation_token": prepared.get("confirmation_token")
                or "exp_" + "0" * 64,
                "confirm_local_export": confirm,
            },
        )

    def wait_task(self, response, *, timeout: float = 5) -> dict:
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.client.get(f"/api/tasks/{task['task_id']}").json()
            if task["status"] not in {"QUEUED", "RUNNING"}:
                return task
            time.sleep(0.01)
        self.fail("Final Export task did not finish")

    def export_once(self) -> dict:
        task = self.wait_task(self.execute())
        self.assertEqual(task["status"], "SUCCEEDED", task)
        return task

    def task_count(self) -> int:
        return len(
            self.client.get(f"/api/projects/{self.project_id}/tasks").json()["tasks"]
        )

    def update_project(self, callback) -> None:
        payload = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        callback(payload)
        self.paths.save_json(self.paths.project_state_path(), payload)

    def update_active_voice(self, callback) -> None:
        manifest = VoiceAssetManager(self.paths).load_manifest()
        active = manifest["active_version"]
        entry = next(item for item in manifest["versions"] if item["version"] == active)
        callback(entry)
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)

    def update_active_subtitle(self, callback) -> None:
        manifest = SubtitleAssetManager(self.paths).load_manifest()
        active = manifest["active_version"]
        entry = next(item for item in manifest["versions"] if item["version"] == active)
        callback(entry)
        self.paths.save_json(self.paths.subtitle_manifest_path(), manifest)

    def stale_codes(self) -> list[str]:
        return self.client.get(self.base).json().get("stale_reasons", [])

    # PREFLIGHT
    def test_01_assembly_ready(self):
        payload = self.preflight().json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["inputs"]["assembly_version"], 1)

    def test_02_selects_active_voice_subtitle_and_music(self):
        inputs = self.preflight().json()["inputs"]
        self.assertEqual(inputs, {"assembly_version": 1, "voice_version": 1, "subtitle_version": 1, "music_version": 1})

    def test_03_music_is_optional(self):
        manifest = MusicAssetManager(self.paths).load_manifest()
        manifest["active_version"] = None
        self.paths.save_json(self.paths.music_manifest_path(), manifest)
        payload = self.preflight().json()
        self.assertTrue(payload["ready"])
        self.assertIsNone(payload["inputs"]["music_version"])

    def test_04_no_voice_obeys_narration_state(self):
        manifest = VoiceAssetManager(self.paths).load_manifest()
        manifest["active_version"] = None
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        payload = self.preflight().json()
        self.assertFalse(payload["ready"])
        self.assertIn("ACTIVE_VOICE_REQUIRED", [item["code"] for item in payload["issues"]])
        self.paths.save_json(self.paths.creative_brief_path(), {"narration_plan": {"enabled": False}})
        self.assertNotIn("ACTIVE_VOICE_REQUIRED", [item["code"] for item in self.preflight().json()["issues"]])

    def test_05_voice_timing_pass(self):
        self.update_active_voice(lambda item: item.update(timing_calibration={"status": "PASS", "voice_track_start": 0.1, "actual_audio_duration": 1.0, "actual_voice_end": 1.1}))
        self.assertEqual(self.preflight().json()["voice_timing"]["status"], "PASS")

    def test_06_out_of_tolerance_without_acceptance_blocks(self):
        self.update_active_voice(lambda item: item.update(timing_calibration={"status": "OUT_OF_TOLERANCE", "voice_track_start": 0.0, "actual_audio_duration": 1.0, "actual_voice_end": 1.0}, timing_acceptance=None))
        payload = self.preflight().json()
        self.assertIn("VOICE_TIMING_ACCEPTANCE_REQUIRED", [item["code"] for item in payload["issues"]])

    def test_07_accepted_out_of_tolerance_is_allowed(self):
        self.update_active_voice(lambda item: item.update(timing_calibration={"status": "OUT_OF_TOLERANCE", "voice_track_start": 0.0, "actual_audio_duration": 1.0, "actual_voice_end": 1.0}, timing_acceptance={"accepted": True}))
        self.assertTrue(self.preflight().json()["ready"])

    def test_08_out_of_bounds_blocks(self):
        self.update_active_voice(lambda item: item.update(timing_calibration={"status": "OUT_OF_BOUNDS", "voice_track_start": 0.0, "actual_audio_duration": 3.0, "actual_voice_end": 3.0}))
        self.assertIn("VOICE_OUT_OF_BOUNDS", [item["code"] for item in self.preflight().json()["issues"]])

    def test_09_narration_caption_matches_voice(self):
        subtitle = self.preflight().json()["subtitle"]
        self.assertEqual(subtitle["semantic_type"], "NARRATION_CAPTION")
        self.assertTrue(subtitle["voice_aligned"])

    def test_10_subtitle_voice_mismatch_blocks(self):
        self.update_active_subtitle(lambda item: item.update(source_voice_version=99))
        payload = self.preflight().json()
        self.assertIn("SUBTITLE_VOICE_MISMATCH", [item["code"] for item in payload["issues"]])

    def test_11_legacy_screen_text_blocks_narration(self):
        self.update_active_subtitle(lambda item: item.update(semantic_type="LEGACY_SCREEN_TEXT", source_voice_version=None))
        self.assertIn("LEGACY_SUBTITLE_NOT_ALIGNED", [item["code"] for item in self.preflight().json()["issues"]])

    def test_12_preflight_does_not_render_ffmpeg(self):
        before = self.runner.ffmpeg_calls
        self.preflight()
        self.assertEqual(self.runner.ffmpeg_calls, before)

    def test_13_preflight_creates_no_task(self):
        self.preflight()
        self.assertEqual(self.task_count(), 0)

    def test_14_next_export_version(self):
        self.assertEqual(self.preflight().json()["next_export_version"], 1)

    # CONFIRM / TASK
    def test_15_confirm_false_creates_no_task_or_render(self):
        response = self.execute(confirm=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.task_count(), self.runner.ffmpeg_calls), (0, 0))

    def test_16_stale_token_creates_no_task_or_render(self):
        prepared = self.preflight().json()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.3)
        response = self.execute(prepared)
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.task_count(), self.runner.ffmpeg_calls), (0, 0))

    def test_17_confirm_true_returns_202(self):
        self.assertEqual(self.execute().status_code, 202)

    def test_18_operation_is_final_export(self):
        self.assertEqual(self.execute().json()["operation"], "FINAL_EXPORT")

    def test_19_final_export_task_is_created(self):
        response = self.execute()
        self.assertEqual(response.headers["location"], f"/api/tasks/{response.json()['task_id']}")

    def test_20_worker_calls_export_current(self):
        original = ExportPipeline.export_current
        with patch.object(ExportPipeline, "export_current", autospec=True, side_effect=original) as called:
            self.export_once()
        called.assert_called_once()

    def test_21_web_does_not_call_private_ffmpeg_builder(self):
        source = Path("web_backend/services/final_export.py").read_text(encoding="utf-8")
        self.assertNotIn("_build_ffmpeg_command", source)

    def test_22_success_updates_checkpoint(self):
        self.export_once()
        component = self.checkpoint().data["post_production"]["components"]["final_export"]
        self.assertEqual((component["status"], component["active_version"]), ("COMPLETED", 1))

    def test_23_worker_uses_project_lock(self):
        response = self.execute()
        task = self.wait_task(response)
        self.assertEqual(task["status"], "SUCCEEDED")

    def test_24_active_task_rejects_duplicate(self):
        from web_backend.models.tasks import TaskResultReference

        release = __import__("threading").Event()
        active = self.app.state.task_service.submit(
            project_id=self.project_id,
            operation=TaskOperation.ASSEMBLY_EXECUTE,
            correlation_id="req_existing",
            callable_=lambda: (release.wait(2), TaskResultReference(resource_type="ASSEMBLY", version=1))[1],
        )
        try:
            response = self.execute()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        finally:
            release.set()
            self.client.get(f"/api/tasks/{active.task_id}")

    # IDEMPOTENCY / INTERRUPTION
    def test_25_identical_export_requires_no_execution(self):
        self.export_once()
        payload = self.preflight().json()
        self.assertFalse(payload["execution_required"])
        self.assertEqual(payload["existing_export_version"], 1)

    def test_26_identical_input_creates_no_second_task(self):
        self.export_once()
        before = self.task_count()
        self.preflight()
        self.assertEqual(self.task_count(), before)

    def test_27_completed_bundle_beats_failed_task(self):
        self.export_once()
        failed = TaskRecord(
            task_id="task_" + "f" * 32,
            project_id=self.project_id,
            operation=TaskOperation.FINAL_EXPORT,
            target_id="export_v001",
            status=TaskStatus.FAILED,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            correlation_id="req_failed",
            error=TaskError(code="FINAL_EXPORT_FAILED", message="任务执行失败。"),
        )
        self.app.state.task_repository.create(failed)
        self.assertFalse(self.preflight().json()["execution_required"])

    def test_28_recovery_does_not_repeat_ffmpeg(self):
        self.export_once()
        before = self.runner.ffmpeg_calls
        self.client.get(self.base)
        self.preflight()
        self.assertEqual(self.runner.ffmpeg_calls, before)

    def test_29_restart_marks_running_interrupted(self):
        running = TaskRecord(
            task_id="task_" + "e" * 32,
            project_id=self.project_id,
            operation=TaskOperation.FINAL_EXPORT,
            target_id="export_v001",
            status=TaskStatus.RUNNING,
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            correlation_id="req_running",
        )
        self.app.state.task_repository.create(running)
        recovered = self.app.state.task_service.recover_interrupted_tasks()
        self.assertEqual(recovered[0].status, TaskStatus.INTERRUPTED)

    def test_30_restart_never_reruns_ffmpeg(self):
        before = self.runner.ffmpeg_calls
        self.app.state.task_service.recover_interrupted_tasks()
        self.assertEqual(self.runner.ffmpeg_calls, before)

    def test_31_retry_needs_new_preflight_and_confirmation(self):
        old = self.preflight().json()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.31)
        self.assertEqual(self.execute(old).status_code, 409)
        self.assertEqual(self.execute(self.preflight().json()).status_code, 202)

    # VERSION
    def test_32_first_export_creates_v001(self):
        self.export_once()
        self.assertTrue(self.paths.export_version_dir(1).is_dir())

    def test_33_changed_input_creates_v002(self):
        self.export_once()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.32)
        self.export_once()
        self.assertTrue(self.paths.export_version_dir(2).is_dir())

    def test_34_v001_is_preserved(self):
        self.export_once()
        original = self.paths.export_version_video_path(1).read_bytes()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.33)
        self.export_once()
        self.assertEqual(self.paths.export_version_video_path(1).read_bytes(), original)

    def test_35_active_becomes_v002(self):
        self.export_once()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.34)
        self.export_once()
        self.assertEqual(ExportAssetManager(self.paths).load_manifest()["active_version"], 2)

    def test_36_failure_creates_no_fake_version(self):
        failing = FakeMediaRunner()
        original = failing.__call__
        def fail(command, **kwargs):
            if "ffmpeg" in Path(command[0]).name.lower() and "-version" not in command:
                return subprocess.CompletedProcess(command, 1, "", "safe failure")
            return original(command, **kwargs)
        self.app.state.final_export_web_service._pipeline_factory = lambda paths, state: ExportPipeline(paths, state, runner=fail, which=lambda name: name)
        task = self.wait_task(self.execute())
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(ExportAssetManager(self.paths).load_manifest()["versions"], [])

    # STALE
    def test_37_assembly_change_is_stale(self):
        self.export_once()
        self.update_project(lambda data: data["assembly"].update(final_video_version=2))
        self.assertIn("ASSEMBLY_CHANGED", self.stale_codes())

    def test_38_voice_change_is_stale(self):
        self.export_once()
        self.add_voice(script="new voice")
        self.assertIn("VOICE_CHANGED", self.stale_codes())

    def test_39_subtitle_change_is_stale(self):
        self.export_once()
        self.add_subtitle()
        self.assertIn("SUBTITLE_CHANGED", self.stale_codes())

    def test_40_music_change_is_stale(self):
        self.export_once()
        self.add_music()
        self.assertIn("MUSIC_CHANGED", self.stale_codes())

    def test_41_music_mix_only_change_is_stale(self):
        self.export_once()
        MusicMixSettingsManager(self.checkpoint()).update(base_volume=0.2)
        self.assertEqual(self.stale_codes(), ["MUSIC_MIX_CHANGED"])

    def test_42_null_to_version_is_stale(self):
        music = MusicAssetManager(self.paths).load_manifest()
        music["active_version"] = None
        self.paths.save_json(self.paths.music_manifest_path(), music)
        self.export_once()
        music["active_version"] = 1
        self.paths.save_json(self.paths.music_manifest_path(), music)
        self.assertIn("MUSIC_CHANGED", self.stale_codes())

    def test_43_version_to_null_is_stale(self):
        self.export_once()
        music = MusicAssetManager(self.paths).load_manifest()
        music["active_version"] = None
        self.paths.save_json(self.paths.music_manifest_path(), music)
        self.assertIn("MUSIC_CHANGED", self.stale_codes())

    def test_44_multiple_stale_reasons(self):
        self.export_once()
        self.add_voice(script="v2")
        self.add_music()
        reasons = self.stale_codes()
        self.assertIn("VOICE_CHANGED", reasons)
        self.assertIn("MUSIC_CHANGED", reasons)

    def test_45_fresh_has_no_stale_reason(self):
        self.export_once()
        payload = self.client.get(self.base).json()
        self.assertFalse(payload["stale"])
        self.assertEqual(payload["stale_reasons"], [])

    # SUBTITLE SEMANTICS
    def test_46_matching_source_voice_is_fresh(self):
        self.export_once()
        self.assertFalse(self.preflight().json()["stale"])

    def test_47_voice_change_makes_subtitle_blocker(self):
        self.export_once()
        self.add_voice(script="v2")
        payload = self.preflight().json()
        self.assertIn("SUBTITLE_VOICE_MISMATCH", [item["code"] for item in payload["issues"]])

    def test_48_export_never_auto_generates_subtitle(self):
        with patch("subtitle_generation.generate_subtitle_for_project") as generated:
            self.export_once()
        generated.assert_not_called()

    # HISTORY
    def test_49_history_lists_versions(self):
        self.export_once()
        payload = self.client.get(f"{self.base}/history").json()
        self.assertEqual([item["version"] for item in payload["versions"]], [1])

    def test_50_history_marks_active(self):
        self.export_once()
        self.assertTrue(self.client.get(f"{self.base}/history").json()["versions"][0]["is_active"])

    def test_51_version_detail_is_safe(self):
        self.export_once()
        payload = self.client.get(f"{self.base}/versions/1").json()
        self.assertEqual(payload["assembly_version"], 1)
        assert_public_payload(self, payload)

    def test_52_version_video_200(self):
        self.export_once()
        response = self.client.get(f"{self.base}/versions/1/video")
        self.assertEqual((response.status_code, response.headers["content-type"]), (200, "video/mp4"))

    def test_53_version_video_range_206(self):
        self.export_once()
        response = self.client.get(f"{self.base}/versions/1/video", headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 4)

    def test_54_version_traversal_is_rejected(self):
        response = self.client.get(f"{self.base}/versions/..%252f1/video")
        self.assertNotEqual(response.status_code, 200)

    # 202 SAFETY CONTRACT
    def test_55_frontend_operation_contract_has_final_export(self):
        source = Path("frontend/src/api/types.ts").read_text(encoding="utf-8")
        self.assertIn('"FINAL_EXPORT"', source)

    def test_56_accepted_contract_uses_one_execute_post(self):
        source = Path("web_backend/routers/final_export.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"/projects/{project_id}/export/execute"'), 1)

    def test_57_location_reconciliation_contract(self):
        response = self.execute()
        self.assertRegex(response.headers["location"], r"^/api/tasks/task_[0-9a-f]{32}$")

    def test_58_project_task_fallback_exposes_task(self):
        accepted = self.execute().json()
        tasks = self.client.get(f"/api/projects/{self.project_id}/tasks").json()["tasks"]
        self.assertIn(accepted["task_id"], [item["task_id"] for item in tasks])

    # SECURITY
    def test_59_preflight_has_no_paths(self):
        assert_public_payload(self, self.preflight().json())

    def test_60_response_has_no_ffmpeg_command(self):
        self.assertNotIn("ffmpeg", json.dumps(self.preflight().json()).casefold())

    def test_61_response_has_no_fingerprint(self):
        rendered = json.dumps(self.preflight().json()).casefold()
        self.assertNotIn("fingerprint", rendered)

    def test_62_response_has_no_hash(self):
        rendered = json.dumps(self.preflight().json()).casefold()
        self.assertNotIn("sha256", rendered)
        self.assertNotIn('"sha"', rendered)

    def test_63_task_dto_is_safe(self):
        assert_public_payload(self, self.execute().json())

    # NO PROVIDER / EXTERNAL CALLS
    def test_64_minimax_is_never_called(self):
        with patch("requests.Session.request", side_effect=AssertionError("network")):
            self.preflight()

    def test_65_deepseek_is_never_called(self):
        with patch("requests.request", side_effect=AssertionError("network")):
            self.preflight()

    def test_66_tts_is_never_called(self):
        with patch.object(LocalVoiceProvider, "generate_voice", side_effect=AssertionError("tts")):
            self.preflight()

    def test_67_provider_network_is_never_called(self):
        original_connection = socket.create_connection
        def blocked(*_args, **_kwargs):
            raise AssertionError("provider network")
        socket.create_connection = blocked
        try:
            self.preflight()
        finally:
            socket.create_connection = original_connection

    def test_68_workflow_treats_missing_music_mix_lineage_as_stale(self):
        self.export_once()
        manifest = ExportAssetManager(self.paths).load_manifest()
        manifest["versions"][0].pop("music_mix")
        self.paths.save_json(self.paths.export_manifest_path(), manifest)
        payload = self.client.get(f"/api/projects/{self.project_id}/workflow").json()
        self.assertEqual(payload["stages"]["export"]["status"], "STALE")

    def test_69_history_suppresses_unsafe_persisted_text(self):
        self.export_once()
        manifest = ExportAssetManager(self.paths).load_manifest()
        manifest["versions"][0]["created_at"] = r"D:\private\export.mp4"
        self.paths.save_json(self.paths.export_manifest_path(), manifest)
        payload = self.client.get(f"{self.base}/history").json()
        self.assertIsNone(payload["versions"][0]["created_at"])
        self.assertNotIn(r"D:\private", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
