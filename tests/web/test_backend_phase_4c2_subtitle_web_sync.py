from __future__ import annotations

import io
import json
import socket
import subprocess
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from post_production import ProjectCompletionStatus
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from subtitle_assets import SubtitleAssetError, SubtitleAssetManager
from subtitle_generation import load_storyboard_subtitle_source
from subtitle_provider_registry import build_subtitle_provider_registry
from tests.test_storyboard_subtitle_provider import compiled_storyboard
from tests.web.web_response_assertions import assert_public_payload
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)
from web_backend.models.subtitle import SubtitleGenerateRequest
from web_backend.models.tasks import TaskOperation, TaskResultReference


def silent_wav(duration: float = 1.0, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * int(duration * sample_rate))
    return buffer.getvalue()


class LocalVoiceProvider(VoiceProvider):
    provider_name = "local_voice"
    model_name = "local"
    api_version = "local"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self, duration: float = 1.0) -> None:
        self.duration = duration

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        return VoiceGenerationResult(audio_bytes=silent_wav(self.duration))


class WebBackendPhase4C2SubtitleWebSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.project_dir = self.projects_root / "subtitle-project"
        self.paths = create_project_paths(self.project_dir)
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Subtitle Web",
            {
                "product_name": "字幕",
                "product_description": "本地生成",
                "user_notes": "",
                "duration_seconds": 12,
            },
        )
        checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "total_duration": 12.0,
            }
        )
        checkpoint.data["completion_status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        checkpoint.save()
        self.project_id = str(checkpoint.data["project_id"])
        self.paths.final_video_path().write_bytes(b"video")
        self.paths.save_json(
            self.paths.storyboard_file_path(),
            compiled_storyboard(),
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "narration_plan": {"enabled": True},
                "av_timeline_constraints": {"forbidden_windows": []},
            },
        )
        self.create_voice("actual Voice script first.\nactual Voice script second.")

        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.app = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.root / "runtime",
                task_workers=1,
            )
        )
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.task_runner.shutdown)

    @property
    def base(self) -> str:
        return f"/api/projects/{self.project_id}/post-production/subtitle"

    def options(self) -> dict:
        response = self.client.get(f"{self.base}/options")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action_payload(self) -> dict:
        options = self.options()
        return {
            "expected_active_version": options["active_version"],
            "expected_next_version": options["next_version"],
            "expected_voice_version": options["source"]["voice_version"],
        }

    def generate(self, *, regenerate: bool = False):
        path = "regenerate" if regenerate else "generate"
        return self.client.post(f"{self.base}/{path}", json=self.action_payload())

    def create_voice(self, script: str, *, duration: float = 1.0) -> None:
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                script=script,
                voice="local",
                language="zh-CN",
                settings={
                    "script_source": "compiled_storyboard",
                    "planned_narration_duration": duration,
                    "planned_voice_span": duration,
                    "planned_first_voice_start": 1.305,
                    "total_video_duration": 12.0,
                    "source_storyboard_path": "storyboard/compiled_storyboard.json",
                },
            ),
            LocalVoiceProvider(duration),
        )

    def task_count(self) -> int:
        payload = self.client.get(
            f"/api/projects/{self.project_id}/tasks"
        ).json()
        return len(payload["tasks"])

    def create_legacy_storyboard_subtitle(self) -> None:
        source = load_storyboard_subtitle_source(self.paths)
        assert source is not None
        registry = build_subtitle_provider_registry()
        SubtitleAssetManager(self.paths).generate_and_save(
            source.request,
            registry.resolve(source.request),
            source_voice_version=None,
            source_script_path=None,
            source_audio_path=None,
            source_storyboard_path=source.storyboard_path,
        )

    def test_01_active_voice_source_is_ready(self):
        payload = self.options()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["source"]["type"], "active_voice")
        self.assertEqual(payload["source"]["semantic_type"], "NARRATION_CAPTION")

    def test_02_active_voice_timing_is_exposed(self):
        payload = self.options()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["source"]["voice_version"], 1)
        self.assertEqual(payload["source"]["voice_track_start"], 1.305)
        self.assertEqual(payload["source"]["actual_audio_duration"], 1.0)

    def test_03_no_active_voice_is_not_ready(self):
        manifest = VoiceAssetManager(self.paths).load_manifest()
        manifest["active_version"] = None
        manifest["versions"] = []
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        payload = self.options()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["issues"][0]["code"], "ACTIVE_VOICE_REQUIRED")

    def test_04_storyboard_screen_copy_is_ignored(self):
        source = self.options()["source"]
        self.assertIn("actual Voice script first", source["script"])
        self.assertNotIn("砰！", source["script"])

    def test_05_next_version_starts_at_one(self):
        self.assertEqual(self.options()["next_version"], 1)

    def test_06_options_dto_is_path_free(self):
        payload = self.options()
        assert_public_payload(self, payload)
        self.assertNotIn("path", json.dumps(payload).lower())

    def test_07_generate_is_synchronous_200(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200, response.text)

    def test_08_generate_creates_no_task(self):
        self.assertEqual(self.task_count(), 0)
        self.assertEqual(self.generate().status_code, 200)
        self.assertEqual(self.task_count(), 0)

    def test_09_generate_has_zero_external_provider_network(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network")),
        ):
            self.assertEqual(self.generate().status_code, 200)

    def test_10_generate_never_calls_ai(self):
        with (
            patch(
                "web_backend.services.capabilities.CapabilityService.deepseek_api_key",
                side_effect=AssertionError("DeepSeek called"),
            ),
            patch(
                "providers.minimax_hailuo_provider.MiniMaxHailuoProvider.submit",
                side_effect=AssertionError("MiniMax Hailuo called"),
            ),
            patch(
                "providers.minimax_h3_provider.MiniMaxH3Provider.submit",
                side_effect=AssertionError("MiniMax H3 called"),
            ),
        ):
            self.assertEqual(self.generate().status_code, 200)

    def test_11_generate_never_runs_ffmpeg(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("ffmpeg")):
            self.assertEqual(self.generate().status_code, 200)

    def test_12_generate_creates_v001(self):
        payload = self.generate().json()
        self.assertEqual(payload["version"], 1)
        self.assertTrue(self.paths.subtitle_version_srt_path(1).is_file())

    def test_13_generate_sets_active_version(self):
        self.generate()
        self.assertEqual(SubtitleAssetManager(self.paths).load_manifest()["active_version"], 1)

    def test_14_generate_uses_checkpoint_callable(self):
        with patch(
            "web_backend.services.subtitle.PostProductionPipeline.mark_component_completed",
            autospec=True,
        ) as completed:
            self.assertEqual(self.generate().status_code, 200)
        completed.assert_called_once()

    def test_15_regenerate_creates_v002(self):
        self.generate()
        payload = self.generate(regenerate=True).json()
        self.assertEqual(payload["version"], 2)

    def test_16_regenerate_preserves_v001(self):
        self.generate()
        before = self.paths.subtitle_version_srt_path(1).read_bytes()
        self.generate(regenerate=True)
        self.assertEqual(self.paths.subtitle_version_srt_path(1).read_bytes(), before)

    def test_17_regenerate_sets_active_two(self):
        self.generate()
        self.generate(regenerate=True)
        self.assertEqual(SubtitleAssetManager(self.paths).load_manifest()["active_version"], 2)

    def test_18_regenerate_creates_no_task(self):
        self.generate()
        self.generate(regenerate=True)
        self.assertEqual(self.task_count(), 0)

    def test_19_voice_absolute_times_are_preserved(self):
        cues = self.generate().json()["cues"]
        self.assertEqual(cues[0]["start"], "00:00:01,305")
        self.assertEqual(cues[-1]["end"], "00:00:02,305")

    def test_20_latest_active_voice_generates_cues(self):
        self.create_voice("new active Voice script")
        payload = self.generate().json()
        self.assertGreater(payload["cue_count"], 0)
        self.assertEqual(payload["source_voice_version"], 2)
        self.assertEqual(payload["semantic_type"], "NARRATION_CAPTION")

    def test_21_timing_source_is_core_lineage(self):
        self.assertEqual(
            self.generate().json()["timing_source"],
            "voice_audio_duration",
        )

    def test_22_history_lists_versions(self):
        self.generate()
        self.generate(regenerate=True)
        payload = self.client.get(f"{self.base}/history").json()
        self.assertEqual([item["version"] for item in payload["versions"]], [2, 1])

    def test_23_history_marks_active(self):
        self.generate()
        self.generate(regenerate=True)
        payload = self.client.get(f"{self.base}/history").json()
        self.assertTrue(payload["versions"][0]["is_active"])
        self.assertFalse(payload["versions"][1]["is_active"])

    def test_24_v1_detail_is_available(self):
        self.generate()
        response = self.client.get(f"{self.base}/versions/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], 1)

    def test_25_v2_detail_is_available(self):
        self.generate()
        self.generate(regenerate=True)
        self.assertEqual(self.client.get(f"{self.base}/versions/2").json()["version"], 2)

    def test_26_version_detail_contains_cue_preview(self):
        self.generate()
        self.assertGreater(
            len(self.client.get(f"{self.base}/versions/1").json()["cues"]),
            0,
        )

    def test_27_project_lock_busy_is_409(self):
        with self.app.state.project_lock_manager.project_write(self.project_id):
            response = self.client.post(f"{self.base}/generate", json=self.action_payload())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")

    def test_28_concurrent_same_project_does_not_duplicate_v001(self):
        from web_backend.services.projects import ProjectBusy

        service = self.app.state.subtitle_web_service
        payload = SubtitleGenerateRequest(
            expected_active_version=None,
            expected_next_version=1,
            expected_voice_version=1,
        )
        entered = threading.Event()
        release = threading.Event()
        from web_backend.services import subtitle as subtitle_service

        original = subtitle_service.generate_subtitle_for_project

        def blocked(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        with patch.object(subtitle_service, "generate_subtitle_for_project", side_effect=blocked):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(service.generate, self.project_id, payload, regenerate=False)
                self.assertTrue(entered.wait(timeout=2))
                with self.assertRaises(ProjectBusy):
                    service.generate(self.project_id, payload, regenerate=False)
                release.set()
                first.result(timeout=3)
        self.assertEqual(len(SubtitleAssetManager(self.paths).load_manifest()["versions"]), 1)

    def test_29_active_web_task_returns_project_busy(self):
        release = threading.Event()
        self.app.state.task_service.submit(
            project_id=self.project_id,
            operation=TaskOperation.VOICE_GENERATE,
            target_id="voice_v999",
            correlation_id=None,
            callable_=lambda: (
                release.wait(timeout=2)
                or TaskResultReference(resource_type="TEST", resource_id="done")
            ),
        )
        response = self.client.post(f"{self.base}/generate", json=self.action_payload())
        release.set()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")

    def test_30_active_voice_required_is_safe_409(self):
        manifest = VoiceAssetManager(self.paths).load_manifest()
        manifest["active_version"] = None
        manifest["versions"] = []
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        response = self.client.post(
            f"{self.base}/generate",
            json={
                "expected_active_version": None,
                "expected_next_version": 1,
                "expected_voice_version": 1,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTIVE_VOICE_REQUIRED")

    def test_31_core_failure_creates_no_completed_version(self):
        with patch(
            "web_backend.services.subtitle.generate_subtitle_for_project",
            side_effect=SubtitleAssetError("internal path"),
        ):
            response = self.generate()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(SubtitleAssetManager(self.paths).active_version())

    def test_32_core_failure_preserves_old_active(self):
        self.generate()
        with patch(
            "web_backend.services.subtitle.generate_subtitle_for_project",
            side_effect=SubtitleAssetError("internal path"),
        ):
            response = self.generate(regenerate=True)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(SubtitleAssetManager(self.paths).load_manifest()["active_version"], 1)

    def test_33_version_traversal_is_rejected(self):
        response = self.client.get(f"{self.base}/versions/%2E%2E%2Fproject.json")
        self.assertIn(response.status_code, {404, 422})

    def test_34_all_subtitle_dtos_are_path_free(self):
        self.generate()
        for suffix in ("", "/options", "/history", "/versions/1"):
            assert_public_payload(self, self.client.get(f"{self.base}{suffix}").json())

    def test_35_no_sha_fingerprint_or_credential(self):
        self.generate()
        payload = json.dumps(self.client.get(f"{self.base}/history").json()).lower()
        for forbidden in ("sha256", "fingerprint", "credential", "api_key"):
            self.assertNotIn(forbidden, payload)

    def test_36_generation_does_not_create_music(self):
        self.generate()
        self.assertFalse(self.paths.music_manifest_path().exists())

    def test_37_generation_does_not_create_export(self):
        self.generate()
        self.assertFalse(self.paths.export_manifest_path().exists())

    def test_38_generation_does_not_call_tts(self):
        with patch.object(
            LocalVoiceProvider,
            "generate_voice",
            side_effect=AssertionError("TTS called"),
        ):
            self.assertEqual(self.generate().status_code, 200)

    def test_39_narration_disabled_is_not_applicable(self):
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {"narration_plan": {"enabled": False}},
        )
        options = self.options()
        self.assertFalse(options["applicable"])
        self.assertFalse(options["ready"])
        self.assertIsNone(options["source"])
        response = self.client.post(
            f"{self.base}/generate",
            json={
                "expected_active_version": None,
                "expected_next_version": 1,
                "expected_voice_version": 1,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "SUBTITLE_NOT_APPLICABLE")

    def test_40_voice_change_marks_subtitle_stale_without_auto_generation(self):
        self.generate()
        self.create_voice("replacement active Voice")
        manifest = SubtitleAssetManager(self.paths).load_manifest()
        self.assertEqual(len(manifest["versions"]), 1)
        options = self.options()
        self.assertTrue(options["stale"])
        self.assertEqual(options["stale_reason"], "VOICE_VERSION_CHANGED")

    def test_41_expected_voice_version_prevents_source_drift(self):
        old_options = self.options()
        self.create_voice("replacement active Voice")
        response = self.client.post(
            f"{self.base}/generate",
            json={
                "expected_active_version": old_options["active_version"],
                "expected_next_version": old_options["next_version"],
                "expected_voice_version": old_options["source"]["voice_version"],
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "SUBTITLE_SOURCE_CHANGED")
        self.assertIsNone(SubtitleAssetManager(self.paths).active_version())

    def test_42_generated_text_equals_active_voice_script(self):
        payload = self.generate().json()
        combined = " ".join(cue["text"] for cue in payload["cues"])
        self.assertEqual(
            "".join(combined.split()),
            "".join("actual Voice script first. actual Voice script second.".split()),
        )

    def test_43_legacy_screen_text_is_stale(self):
        self.create_legacy_storyboard_subtitle()
        options = self.options()
        self.assertTrue(options["stale"])
        self.assertEqual(options["stale_reason"], "LEGACY_SCREEN_TEXT")

    def test_44_old_legacy_bundle_semantic_is_derived(self):
        self.create_legacy_storyboard_subtitle()
        manifest = SubtitleAssetManager(self.paths).load_manifest()
        manifest["versions"][0].pop("semantic_type", None)
        self.paths.save_json(self.paths.subtitle_manifest_path(), manifest)
        config_path = self.paths.subtitle_version_config_path(1)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("semantic_type", None)
        self.paths.save_json(config_path, config)
        history = self.client.get(f"{self.base}/history").json()
        self.assertEqual(
            history["versions"][0]["semantic_type"],
            "LEGACY_SCREEN_TEXT",
        )

    def test_45_new_subtitle_records_absolute_voice_metadata(self):
        payload = self.generate().json()
        self.assertEqual(payload["actual_audio_duration"], 1.0)
        self.assertEqual(payload["voice_track_start"], 1.305)
        self.assertAlmostEqual(payload["actual_voice_end"], 2.305)
        self.assertFalse(payload["cue_level_alignment"])


if __name__ == "__main__":
    unittest.main()
