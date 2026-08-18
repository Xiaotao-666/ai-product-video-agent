from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from post_production import (
    PostProductionPipeline,
    PostProductionStage,
    PostProductionStatus,
)
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)
from voice_provider_registry import VoiceProviderRegistry


def silent_wav(duration_seconds: float = 0.05, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    frames = b"\x00\x00" * max(1, int(duration_seconds * sample_rate))
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(frames)
    return buffer.getvalue()


class MockVoiceProvider(VoiceProvider):
    provider_name = "mock"
    model_name = "mock-voice-v1"
    api_version = "mock-v1"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN", "en-US"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self) -> None:
        self.calls = 0

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        return VoiceGenerationResult(
            audio_bytes=silent_wav(),
            duration_seconds=0.05,
            provider_task_id=f"mock-task-{self.calls}",
            metadata={"mock": True},
        )


class VoicePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.provider = MockVoiceProvider()
        self.request = VoiceGenerationRequest(
            script="欢迎了解我们的产品。",
            voice="warm-narrator",
            language="zh-CN",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def checkpoint(self) -> ProjectCheckpoint:
        return ProjectCheckpoint.create(
            self.paths,
            "Voice Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )

    def test_A_mock_provider_registration_and_metadata(self):
        registry = VoiceProviderRegistry({"default_provider": "mock"})
        registry.register(self.provider)
        resolved = registry.resolve(self.request)
        self.assertIs(resolved, self.provider)
        self.assertEqual(registry.get_metadata()[0]["model"], "mock-voice-v1")
        result = registry.generate_voice(self.request)
        self.assertEqual(result.provider_task_id, "mock-task-1")
        self.assertEqual(self.provider.calls, 1)

    def test_B_audio_versions_are_immutable_and_project_local(self):
        manager = VoiceAssetManager(self.paths)
        first = manager.generate_and_save(self.request, self.provider)
        first_audio = self.paths.voice_version_audio_path(1)
        first_bytes = first_audio.read_bytes()
        second = manager.generate_and_save(self.request, self.provider)

        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(first_audio.read_bytes(), first_bytes)
        self.assertTrue(self.paths.voice_version_audio_path(2).is_file())
        self.assertEqual(manager.load_manifest()["active_version"], 2)
        self.assertTrue(self.paths.voice_script_history_path(1).is_file())
        for entry in manager.load_manifest()["versions"]:
            audio = self.paths.ensure_within_project(
                self.paths.project_path / entry["audio_path"]
            )
            self.assertTrue(audio.is_file())

    def test_C_new_project_contains_disabled_voice_and_postproduction_skeleton(self):
        checkpoint = self.checkpoint()
        self.assertEqual(
            checkpoint.data["voice_config"],
            {
                "enabled": False,
                "provider": None,
                "voice": None,
                "language": "zh-CN",
            },
        )
        post = checkpoint.data["post_production"]
        self.assertEqual(post["status"], PostProductionStatus.NOT_STARTED.value)
        self.assertEqual(
            list(post["stages"]),
            [stage.value for stage in PostProductionStage],
        )

    def test_D_old_schema2_project_is_backfilled_without_voice_generation(self):
        checkpoint = self.checkpoint()
        checkpoint.ensure_shots([1])
        video_before = deepcopy(checkpoint.data["video_generation"])
        assembly_before = deepcopy(checkpoint.data["assembly"])
        stored = deepcopy(checkpoint.data)
        stored.pop("voice_config")
        stored.pop("post_production")
        self.paths.save_json(self.paths.project_state_path(), stored)

        loaded = ProjectCheckpoint.load(self.paths)
        self.assertFalse(loaded.data["voice_config"]["enabled"])
        self.assertIn("post_production", loaded.data)
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.paths.voice_manifest_path().exists())
        self.assertEqual(loaded.data["video_generation"], video_before)
        self.assertEqual(loaded.data["assembly"], assembly_before)

    def test_E_resume_uses_voice_manifest_without_repeating_provider_call(self):
        manager = VoiceAssetManager(self.paths)
        manager.generate_and_save(self.request, self.provider)
        self.assertEqual(self.provider.calls, 1)

        resumed_manager = VoiceAssetManager(create_project_paths(self.paths.project_path))
        active = resumed_manager.active_version()
        self.assertEqual(active["version"], 1)
        self.assertEqual(active["provider_task_id"], "mock-task-1")
        self.assertEqual(self.provider.calls, 1)

    def test_F_postproduction_state_does_not_mutate_video_or_assembly(self):
        checkpoint = self.checkpoint()
        video_before = deepcopy(checkpoint.data["video_generation"])
        assembly_before = deepcopy(checkpoint.data["assembly"])
        pipeline = PostProductionPipeline(checkpoint)

        pipeline.mark_running(PostProductionStage.VIDEO_ASSEMBLY)
        pipeline.mark_completed(PostProductionStage.VIDEO_ASSEMBLY)
        pipeline.mark_running(PostProductionStage.AUDIO_PROCESSING)
        pipeline.mark_completed(PostProductionStage.AUDIO_PROCESSING)
        reloaded = ProjectCheckpoint.load(self.paths)

        self.assertEqual(reloaded.data["video_generation"], video_before)
        self.assertEqual(reloaded.data["assembly"], assembly_before)
        self.assertEqual(
            reloaded.data["post_production"]["stages"]["AUDIO_PROCESSING"][
                "status"
            ],
            PostProductionStatus.COMPLETED.value,
        )

    def test_G_voice_manifest_contains_no_secret_fields(self):
        VoiceAssetManager(self.paths).generate_and_save(self.request, self.provider)
        raw = self.paths.voice_manifest_path().read_text(encoding="utf-8")
        manifest = json.loads(raw)
        self.assertNotIn("api_key", raw.lower())
        self.assertNotIn("authorization", raw.lower())
        self.assertEqual(manifest["versions"][0]["provider"], "mock")


if __name__ == "__main__":
    unittest.main()
