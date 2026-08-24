from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from post_production_menu import post_production_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from providers.script_subtitle_provider import ScriptSubtitleProvider
from subtitle_assets import SubtitleAssetManager
from subtitle_generation import generate_subtitle_from_active_voice
from subtitle_provider import SubtitleGenerationRequest
from subtitle_provider_registry import (
    SubtitleProviderRegistry,
    build_subtitle_provider_registry,
)
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)


TIMELINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def silent_wav(duration_seconds: float = 2.0, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(
            b"\x00\x00" * max(1, int(duration_seconds * sample_rate))
        )
    return buffer.getvalue()


class LocalVoiceProvider(VoiceProvider):
    provider_name = "local_voice"
    model_name = "local"
    api_version = "local"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        return VoiceGenerationResult(
            audio_bytes=silent_wav(),
            duration_seconds=2.0,
            provider_task_id="local-voice-001",
        )


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


def timestamp_ms(values: tuple[str, ...]) -> tuple[int, int]:
    numbers = [int(value) for value in values]

    def convert(parts: list[int]) -> int:
        return ((parts[0] * 60 + parts[1]) * 60 + parts[2]) * 1000 + parts[3]

    return convert(numbers[:4]), convert(numbers[4:])


class SubtitlePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.voice_request = VoiceGenerationRequest(
            script="小蓝饮料，\n年轻又清爽。",
            voice="local",
            language="zh-CN",
        )
        VoiceAssetManager(self.paths).generate_and_save(
            self.voice_request,
            LocalVoiceProvider(),
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Subtitle Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        self.paths.final_video_path().write_bytes(b"mock-video")
        self.checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "assembled_at": "2026-08-17T10:00:00+08:00",
                "total_duration": 2.0,
            }
        )
        self.checkpoint.save()
        self.logger = TaskLogger(self.paths, "subtitle-test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_A_script_provider_is_registered_and_uses_no_external_api(self):
        registry = build_subtitle_provider_registry()
        request = SubtitleGenerationRequest("测试字幕。", 1.0)
        provider = registry.resolve(request)
        self.assertIsInstance(provider, ScriptSubtitleProvider)
        metadata = provider.get_metadata()
        self.assertEqual(metadata["provider"], "script_subtitle")
        self.assertFalse(metadata["external_api"])

    def test_B_subtitle_file_and_config_are_generated_from_active_voice(self):
        entry = generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["source_voice_version"], 1)
        srt_path = self.paths.subtitle_version_srt_path(1)
        config_path = self.paths.subtitle_version_config_path(1)
        self.assertTrue(srt_path.is_file())
        self.assertTrue(config_path.is_file())
        srt = srt_path.read_text(encoding="utf-8")
        self.assertIn("小蓝饮料，", srt)
        self.assertIn("年轻又清爽。", srt)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["provider"], "script_subtitle")
        self.assertEqual(config["source_voice_version"], 1)
        self.assertEqual(config["timing_source"], "voice_audio_duration")
        self.assertEqual(config["semantic_type"], "NARRATION_CAPTION")
        self.assertFalse(config["cue_level_alignment"])

    def test_C_timeline_is_ordered_and_never_exceeds_audio_duration(self):
        entry = generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )
        text = self.paths.subtitle_version_srt_path(1).read_text(encoding="utf-8")
        intervals = [timestamp_ms(match.groups()) for match in TIMELINE.finditer(text)]
        self.assertEqual(len(intervals), entry["cue_count"])
        previous_end = 0
        for start, end in intervals:
            self.assertGreaterEqual(start, previous_end)
            self.assertGreater(end, start)
            self.assertLessEqual(end, 2000)
            previous_end = end
        self.assertEqual(intervals[-1][1], 2000)

    def test_D_versions_are_immutable(self):
        manager = SubtitleAssetManager(self.paths)
        registry = build_subtitle_provider_registry()
        generate_subtitle_from_active_voice(manager, registry)
        v1 = self.paths.subtitle_version_srt_path(1).read_bytes()
        generate_subtitle_from_active_voice(manager, registry)
        self.assertEqual(manager.load_manifest()["active_version"], 2)
        self.assertEqual(self.paths.subtitle_version_srt_path(1).read_bytes(), v1)
        self.assertTrue(self.paths.subtitle_version_srt_path(2).is_file())

    def test_E_postproduction_menu_generates_subtitle_and_resume_does_not_repeat(self):
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["2", "5"]),
            output_fn=lambda _value: None,
        )
        manager = SubtitleAssetManager(self.paths)
        self.assertEqual(len(manager.load_manifest()["versions"]), 1)
        self.assertEqual(
            self.checkpoint.data["post_production"]["components"]["subtitle"][
                "status"
            ],
            "COMPLETED",
        )

        loaded = ProjectCheckpoint.load(self.paths)
        post_production_menu(
            self.paths,
            loaded,
            self.logger,
            input_fn=Inputs(["2", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(len(manager.load_manifest()["versions"]), 1)

    def test_F_subtitle_pipeline_does_not_change_video_pipeline(self):
        video_before = deepcopy(self.checkpoint.data["video_generation"])
        assembly_before = deepcopy(self.checkpoint.data["assembly"])
        generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.data["video_generation"], video_before)
        self.assertEqual(loaded.data["assembly"], assembly_before)

    def test_G_registry_supports_injected_local_provider_without_network(self):
        provider = ScriptSubtitleProvider(max_chars_per_cue=6)
        registry = SubtitleProviderRegistry({"default_provider": "script_subtitle"})
        registry.register(provider)
        result = registry.generate_subtitle(
            SubtitleGenerationRequest(
                script="一段较长的字幕文本用于本地切分。",
                audio_duration_seconds=3.0,
            )
        )
        self.assertGreater(len(result.cues), 1)
        self.assertEqual(result.cues[-1].end_seconds, 3.0)

    def test_H_active_voice_timing_uses_absolute_srt_and_exact_metadata(self):
        actual_duration = 2.34567
        voice_start = 1.305
        result = ScriptSubtitleProvider().generate_subtitle(
            SubtitleGenerationRequest(
                script="旁白第一句。\n旁白第二句。",
                audio_duration_seconds=actual_duration,
                settings={
                    "source": "active_voice",
                    "semantic_type": "NARRATION_CAPTION",
                    "actual_audio_duration": actual_duration,
                    "voice_track_start": voice_start,
                    "actual_voice_end": voice_start + actual_duration,
                },
            )
        )
        self.assertEqual(result.cues[0].start_seconds, 1.305)
        self.assertEqual(result.cues[-1].end_seconds, 3.651)
        self.assertEqual(result.metadata["actual_audio_duration"], actual_duration)
        self.assertEqual(result.metadata["voice_track_start"], voice_start)
        self.assertEqual(
            result.metadata["actual_voice_end"],
            voice_start + actual_duration,
        )
        self.assertFalse(result.metadata["cue_level_alignment"])


if __name__ == "__main__":
    unittest.main()
