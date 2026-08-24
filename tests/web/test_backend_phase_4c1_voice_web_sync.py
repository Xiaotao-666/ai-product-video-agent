from __future__ import annotations

import io
import json
import socket
import subprocess
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from post_production import ProjectCompletionStatus
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint, ProjectStateError
from tests.test_storyboard_voice_integration import compiled_storyboard
from tests.web.web_response_assertions import assert_public_payload
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
    VoiceProviderError,
)
from voice_provider_registry import VoiceProviderRegistry


def silent_wav(duration: float = 0.2, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * max(1, int(duration * sample_rate)))
    return buffer.getvalue()


class FakeVoiceProvider(VoiceProvider):
    provider_name = "fake_tts"
    model_name = "fake-v1"
    api_version = "fake-v1"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self, *, provider_name: str = "fake_tts") -> None:
        self.provider_name = provider_name
        self.calls = 0
        self.preflights = 0
        self.fail = False
        self.duration = 0.2
        self.scripts: list[str] = []

    def preflight(self, request: VoiceGenerationRequest) -> None:
        self.preflights += 1
        super().preflight(request)

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        self.scripts.append(request.script)
        if self.fail:
            raise VoiceProviderError("raw provider endpoint secret")
        return VoiceGenerationResult(
            audio_bytes=silent_wav(self.duration),
            provider_task_id="provider-locator-must-not-escape",
            metadata={"raw_response": "must-not-escape"},
        )


class WebBackendPhase4C1VoiceWebSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.project_dir = self.projects_root / "voice-project"
        self.paths = create_project_paths(self.project_dir)
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Voice Web",
            {
                "product_name": "柠檬",
                "product_description": "新鲜",
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
        self.paths.final_video_path().write_bytes(b"silent-video")
        self.paths.save_json(
            self.paths.storyboard_file_path(), compiled_storyboard()
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "narration_plan": {
                    "enabled": True,
                    "full_script": "must-not-be-used",
                    "target_duration_seconds": 8.0,
                }
            },
        )

        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.app = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.root / "runtime",
                task_workers=1,
            )
        )
        self.provider = FakeVoiceProvider()
        self.provider_two = FakeVoiceProvider(provider_name="fake_tts_two")
        self.provider_config_revision = 1

        def registry_factory() -> VoiceProviderRegistry:
            registry = VoiceProviderRegistry(
                {
                    "default_provider": "fake_tts",
                    "providers": {
                        "fake_tts": {
                            "enabled": True,
                            "model": "fake-v1",
                            "language": "zh-CN",
                            "default_voice": "xiaoyan",
                            "revision": self.provider_config_revision,
                        },
                        "fake_tts_two": {
                            "enabled": True,
                            "model": "fake-v1",
                            "language": "zh-CN",
                            "default_voice": "xiaoyu",
                        },
                    },
                }
            )
            registry.register(self.provider)
            registry.register(self.provider_two)
            return registry

        self.app.state.voice_web_service._registry_factory = registry_factory
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.task_runner.shutdown)

    @property
    def base(self) -> str:
        return f"/api/projects/{self.project_id}/post-production/voice"

    def options(self):
        response = self.client.get(f"{self.base}/options")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def preflight(
        self,
        *,
        intent: str = "GENERATE",
        script_override: str | None = None,
        provider: str = "fake_tts",
    ) -> dict:
        response = self.client.post(
            f"{self.base}/preflight",
            json={
                "intent": intent,
                "provider": provider,
                "voice": "xiaoyan",
                "language": "zh-CN",
                "script_override": script_override,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, preflight: dict, *, confirm: bool = True):
        intent = preflight["intent"]
        path = "generate" if intent == "GENERATE" else "regenerate"
        return self.client.post(
            f"{self.base}/{path}",
            json={
                "intent": intent,
                "provider": preflight["provider"]["provider_id"],
                "voice": preflight["provider"]["default_voice"] or "xiaoyan",
                "language": preflight["provider"]["language"],
                "script_override": preflight["script"]["text"],
                "preflight_fingerprint": preflight["preflight_fingerprint"],
                "confirm_external_tts_call": confirm,
            },
        )

    def wait_task(self, task_id: str) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            payload = self.client.get(f"/api/tasks/{task_id}").json()
            if payload["status"] not in {"QUEUED", "RUNNING"}:
                return payload
            time.sleep(0.01)
        self.fail("Voice task did not finish")

    def generate(self, *, intent: str = "GENERATE", script: str | None = None):
        prepared = self.preflight(intent=intent, script_override=script)
        response = self.submit(prepared)
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait_task(response.json()["task_id"])

    def task_count(self) -> int:
        return len(self.client.get(f"/api/projects/{self.project_id}/tasks").json()["tasks"])

    def test_01_storyboard_voice_cues_build_global_script(self):
        script = self.options()["script"]
        self.assertEqual(script["cue_count"], 2)
        self.assertEqual(
            script["text"],
            "每一颗柠檬，都带着阳光。\nLEE柠檬，新鲜每一刻。",
        )

    def test_02_manual_fallback_is_supported(self):
        self.paths.storyboard_file_path().unlink()
        self.assertTrue(self.options()["manual_script_required"])
        payload = self.preflight(script_override="手动旁白。")
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["script"]["source"], "manual")

    def test_03_provider_options_are_safe(self):
        payload = self.options()
        self.assertEqual(payload["default_provider"], "fake_tts")
        assert_public_payload(self, payload)
        serialized = json.dumps(payload).lower()
        for forbidden in ("endpoint", "credential", "api_key", "absolute"):
            self.assertNotIn(forbidden, serialized)

    def test_04_next_version_starts_at_one(self):
        self.assertEqual(self.options()["next_version"], 1)

    def test_05_preflight_has_zero_external_network(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network")),
        ):
            payload = self.preflight()
        self.assertTrue(payload["ready"])
        self.assertEqual(self.provider.calls, 0)

    def test_06_script_participates_in_fingerprint(self):
        first = self.preflight(script_override="脚本一")
        second = self.preflight(script_override="脚本二")
        self.assertNotEqual(first["preflight_fingerprint"], second["preflight_fingerprint"])

    def test_07_provider_participates_in_fingerprint(self):
        first = self.preflight(provider="fake_tts")
        second = self.preflight(provider="fake_tts_two")
        self.assertNotEqual(first["preflight_fingerprint"], second["preflight_fingerprint"])

    def test_08_provider_config_participates_in_fingerprint(self):
        first = self.preflight()
        self.provider_config_revision = 2
        second = self.preflight()
        self.assertNotEqual(first["preflight_fingerprint"], second["preflight_fingerprint"])

    def test_09_stale_preflight_creates_zero_task_and_zero_tts(self):
        prepared = self.preflight()
        self.provider_config_revision = 2
        response = self.submit(prepared)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VOICE_PREFLIGHT_STALE")
        self.assertEqual(self.task_count(), 0)
        self.assertEqual(self.provider.calls, 0)

    def test_10_confirm_false_creates_zero_task_and_zero_tts(self):
        response = self.submit(self.preflight(), confirm=False)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "VOICE_EXTERNAL_CONFIRMATION_REQUIRED")
        self.assertEqual(self.task_count(), 0)
        self.assertEqual(self.provider.calls, 0)

    def test_11_confirm_true_returns_202_and_location(self):
        response = self.submit(self.preflight())
        self.assertEqual(response.status_code, 202)
        self.assertRegex(response.headers["location"], r"^/api/tasks/task_")
        self.wait_task(response.json()["task_id"])

    def test_12_generate_uses_voice_generate_operation(self):
        task = self.generate()
        self.assertEqual(task["operation"], "VOICE_GENERATE")

    def test_13_regenerate_still_uses_voice_generate_operation(self):
        self.generate()
        task = self.generate(intent="REGENERATE", script="编辑后的新脚本。")
        self.assertEqual(task["operation"], "VOICE_GENERATE")

    def test_14_worker_uses_existing_voice_asset_manager(self):
        from voice_assets import VoiceAssetManager

        original = VoiceAssetManager.generate_and_save
        calls: list[int] = []

        def recording(manager, request, provider):
            calls.append(1)
            return original(manager, request, provider)

        with patch(
            "web_backend.services.voice.VoiceAssetManager.generate_and_save",
            new=recording,
        ):
            task = self.generate()
        self.assertEqual(task["status"], "SUCCEEDED")
        self.assertEqual(len(calls), 1)

    def test_15_options_use_existing_script_builder_callable(self):
        with patch("web_backend.services.voice.load_storyboard_voice_script", wraps=__import__("voice_script_builder").load_storyboard_voice_script) as mocked:
            self.options()
        self.assertGreaterEqual(mocked.call_count, 1)

    def test_16_preflight_uses_existing_provider_registry(self):
        before = self.provider.preflights
        self.preflight()
        self.assertGreater(self.provider.preflights, before)

    def test_17_generation_uses_existing_calibration(self):
        self.generate()
        detail = self.client.get(self.base).json()
        self.assertEqual(detail["timing_mode"], "whole_track")
        self.assertEqual(detail["calibration_status"], "OUT_OF_TOLERANCE")

    def test_18_generation_marks_existing_checkpoint_component(self):
        self.generate()
        data = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        component = data["post_production"]["components"]["voice"]
        self.assertEqual(component["status"], "COMPLETED")
        self.assertEqual(component["active_version"], 1)

    def test_19_provider_generate_is_called_once(self):
        self.generate()
        self.assertEqual(self.provider.calls, 1)

    def test_20_first_generation_creates_v001(self):
        task = self.generate()
        self.assertEqual(task["result"]["version"], 1)
        self.assertTrue(self.paths.voice_version_dir(1).is_dir())

    def test_21_regenerate_creates_v002(self):
        self.generate()
        task = self.generate(intent="REGENERATE", script="新版脚本。")
        self.assertEqual(task["result"]["version"], 2)

    def test_22_regenerate_does_not_overwrite_v001(self):
        self.generate()
        original = self.paths.voice_version_script_path(1).read_text(encoding="utf-8")
        self.generate(intent="REGENERATE", script="新版脚本。")
        self.assertEqual(self.paths.voice_version_script_path(1).read_text(encoding="utf-8"), original)

    def test_23_regenerate_updates_active_version(self):
        self.generate()
        self.generate(intent="REGENERATE", script="新版脚本。")
        self.assertEqual(self.client.get(self.base).json()["version"], 2)

    def test_24_generation_persists_script_snapshot(self):
        self.generate(intent="GENERATE", script="这次请求的脚本。")
        self.assertEqual(
            self.paths.voice_version_script_path(1).read_text(encoding="utf-8"),
            "这次请求的脚本。",
        )

    def test_25_generation_persists_calibration(self):
        self.generate()
        manifest = json.loads(self.paths.voice_manifest_path().read_text(encoding="utf-8"))
        self.assertIn("timing_calibration", manifest["versions"][0])

    def test_26_provider_failure_creates_no_completed_version(self):
        self.provider.fail = True
        task = self.generate()
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(task["error"]["code"], "VOICE_PROVIDER_FAILED")
        self.assertIsNone(self.client.get(self.base).json()["version"])

    def test_27_provider_failure_keeps_old_active(self):
        self.generate()
        self.provider.fail = True
        task = self.generate(intent="REGENERATE", script="失败版本。")
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(self.client.get(self.base).json()["version"], 1)

    def test_28_provider_failure_does_not_mark_checkpoint_completed_without_bundle(self):
        self.provider.fail = True
        self.generate()
        data = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        self.assertNotEqual(
            data["post_production"]["components"]["voice"]["status"],
            "COMPLETED",
        )

    def test_29_provider_failure_has_no_automatic_retry(self):
        self.provider.fail = True
        self.generate()
        time.sleep(0.05)
        self.assertEqual(self.provider.calls, 1)

    def test_30_frontend_backend_contract_contains_voice_generate(self):
        source = (Path(__file__).parents[2] / "frontend" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
        self.assertIn('"VOICE_GENERATE"', source)

    def test_31_task_payload_contains_no_script(self):
        task = self.generate()
        self.assertNotIn("每一颗柠檬", json.dumps(task, ensure_ascii=False))

    def test_32_interrupted_recovery_does_not_call_tts(self):
        before = self.provider.calls
        recovered = self.app.state.task_service.recover_interrupted_tasks()
        self.assertEqual(recovered, [])
        self.assertEqual(self.provider.calls, before)

    def test_33_durable_business_state_wins_over_failed_task(self):
        prepared = self.preflight()
        with patch(
            "web_backend.services.voice.PostProductionPipeline.mark_component_completed",
            side_effect=ProjectStateError("checkpoint failed"),
        ):
            response = self.submit(prepared)
            task = self.wait_task(response.json()["task_id"])
        self.assertEqual(task["status"], "FAILED")
        self.assertEqual(self.client.get(self.base).json()["version"], 1)

    def test_34_history_is_safe(self):
        self.generate()
        payload = self.client.get(f"{self.base}/history").json()
        self.assertEqual(payload["active_version"], 1)
        assert_public_payload(self, payload)

    def test_35_version_detail_returns_script_snapshot(self):
        self.generate(intent="GENERATE", script="历史脚本。")
        payload = self.client.get(f"{self.base}/versions/1").json()
        self.assertEqual(payload["script"], "历史脚本。")

    def test_36_version_audio_is_200(self):
        self.generate()
        response = self.client.get(f"{self.base}/versions/1/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")

    def test_37_version_audio_supports_range_206(self):
        self.generate()
        response = self.client.get(
            f"{self.base}/versions/1/audio", headers={"Range": "bytes=0-15"}
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 16)

    def test_38_history_marks_only_active_version(self):
        self.generate()
        self.generate(intent="REGENERATE", script="新版脚本。")
        versions = self.client.get(f"{self.base}/history").json()["versions"]
        self.assertEqual([item["version"] for item in versions if item["is_active"]], [2])

    def test_39_legal_timing_acceptance_succeeds(self):
        self.generate()
        response = self.client.post(
            f"{self.base}/timing-acceptance",
            json={"expected_voice_version": 1, "accepted": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["timing_acceptance"]["accepted"])

    def test_40_timing_acceptance_creates_no_task(self):
        self.generate()
        before = self.task_count()
        self.client.post(
            f"{self.base}/timing-acceptance",
            json={"expected_voice_version": 1, "accepted": True},
        )
        self.assertEqual(self.task_count(), before)

    def test_41_timing_acceptance_creates_no_voice_version(self):
        self.generate()
        self.client.post(
            f"{self.base}/timing-acceptance",
            json={"expected_voice_version": 1, "accepted": True},
        )
        self.assertEqual(self.options()["next_version"], 2)

    def test_42_out_of_bounds_timing_cannot_be_accepted(self):
        self.provider.duration = 11.0
        self.generate()
        self.assertEqual(self.client.get(self.base).json()["calibration_status"], "OUT_OF_BOUNDS")
        response = self.client.post(
            f"{self.base}/timing-acceptance",
            json={"expected_voice_version": 1, "accepted": True},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "VOICE_TIMING_ACCEPTANCE_NOT_ALLOWED")

    def test_43_voice_payloads_expose_no_absolute_path(self):
        self.generate()
        for suffix in ("", "/options", "/history", "/versions/1"):
            self.assertNotRegex(self.client.get(f"{self.base}{suffix}").text, r"[A-Z]:[\\/]")

    def test_44_voice_payloads_expose_no_credentials(self):
        payload = self.preflight()
        self.assertNotIn("credential", json.dumps(payload).lower())

    def test_45_voice_payloads_expose_no_provider_locator(self):
        self.generate()
        self.assertNotIn("provider-locator", self.client.get(f"{self.base}/history").text)

    def test_46_voice_failures_expose_no_raw_provider_response(self):
        self.provider.fail = True
        task = self.generate()
        serialized = json.dumps(task).lower()
        self.assertNotIn("endpoint", serialized)
        self.assertNotIn("raw", serialized)

    def test_47_public_task_dto_exposes_no_full_script(self):
        task = self.generate(intent="GENERATE", script="绝不能进入任务记录的完整脚本。")
        self.assertNotIn("完整脚本", json.dumps(task, ensure_ascii=False))

    def test_48_voice_generation_does_not_generate_subtitle(self):
        self.generate()
        self.assertFalse(self.paths.subtitle_manifest_path().exists())

    def test_49_voice_generation_does_not_generate_music(self):
        self.generate()
        self.assertFalse(self.paths.music_manifest_path().exists())

    def test_50_voice_generation_does_not_export(self):
        self.generate()
        self.assertFalse(self.paths.export_manifest_path().exists())

    def test_51_voice_generation_does_not_run_ffmpeg(self):
        with (
            patch.object(subprocess, "run", side_effect=AssertionError("ffmpeg")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("ffmpeg")),
        ):
            task = self.generate()
        self.assertEqual(task["status"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
