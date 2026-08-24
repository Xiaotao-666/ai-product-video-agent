from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from post_production_menu import post_production_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from providers.script_subtitle_provider import ScriptSubtitleProvider
from providers.storyboard_subtitle_provider import StoryboardSubtitleProvider
from subtitle_assets import SubtitleAssetManager
from subtitle_generation import (
    generate_subtitle_for_project,
    load_active_voice_subtitle_source,
    load_storyboard_subtitle_source,
    subtitle_source_label,
)
from subtitle_provider import SubtitleProviderError
from subtitle_provider_registry import build_subtitle_provider_registry
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)


def compiled_storyboard(*, include_cues: bool = True) -> dict:
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "产品亮相",
                "visual": "产品置于清爽背景",
                "camera": "缓慢推进",
                "voiceover_cues": [],
                "subtitle_cues": (
                    [
                        {
                            "text": "清爽开场",
                            "start_offset": 1.0,
                            "end_offset": 2.5,
                            "position": "bottom_center",
                        }
                    ]
                    if include_cues
                    else []
                ),
                "video_constraints": {
                    "reserve_subtitle_space": include_cues,
                    "subtitle_safe_area": (
                        "bottom_center" if include_cues else "none"
                    ),
                },
            },
            {
                "shot_id": 2,
                "duration": 6,
                "purpose": "品牌收束",
                "visual": "包装稳定居中",
                "camera": "固定镜头",
                "voiceover_cues": [],
                "subtitle_cues": (
                    [
                        {
                            "text": "年轻有活力",
                            "start_offset": 0.5,
                            "end_offset": 2.0,
                            "position": "top_center",
                        }
                    ]
                    if include_cues
                    else []
                ),
                "video_constraints": {
                    "reserve_subtitle_space": include_cues,
                    "subtitle_safe_area": "top_center" if include_cues else "none",
                },
            },
        ],
    }


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
            provider_task_id="local-voice",
        )


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class StoryboardSubtitleProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.paths.save_json(
            self.paths.storyboard_file_path(), compiled_storyboard()
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "narration_plan": {"enabled": True},
                "av_timeline_constraints": {"forbidden_windows": []},
            },
        )
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                script="正式旁白脚本。",
                voice="local",
                language="zh-CN",
            ),
            LocalVoiceProvider(),
        )
        self.manager = SubtitleAssetManager(self.paths)
        self.registry = build_subtitle_provider_registry()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _request(self):
        source = load_storyboard_subtitle_source(self.paths)
        self.assertIsNotNone(source)
        return source.request

    def test_01_storyboard_provider_is_registered(self) -> None:
        provider = self.registry.resolve(self._request())
        self.assertIsInstance(provider, StoryboardSubtitleProvider)
        self.assertFalse(provider.get_metadata()["external_api"])

    def test_02_active_voice_has_formal_routing_priority(self) -> None:
        entry = generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(entry["provider"], "script_subtitle")
        self.assertEqual(entry["source"], "active_voice")
        self.assertEqual(entry["semantic_type"], "NARRATION_CAPTION")
        self.assertEqual(subtitle_source_label(self.paths), "Active Voice")

    def test_03_no_subtitle_cues_falls_back_to_script_provider(self) -> None:
        self.paths.save_json(
            self.paths.storyboard_file_path(), compiled_storyboard(include_cues=False)
        )
        entry = generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(entry["provider"], "script_subtitle")
        self.assertEqual(entry["source_voice_version"], 1)
        self.assertEqual(subtitle_source_label(self.paths), "Active Voice")

    def test_04_shot_local_offsets_use_existing_global_timeline(self) -> None:
        result = self.registry.generate_subtitle(self._request())
        self.assertEqual(result.cues[0].start_seconds, 1.0)
        self.assertEqual(result.cues[0].end_seconds, 2.5)
        self.assertEqual(result.cues[1].start_seconds, 6.5)
        self.assertEqual(result.cues[1].end_seconds, 8.0)

    def test_05_multi_shot_cues_keep_storyboard_order(self) -> None:
        result = self.registry.generate_subtitle(self._request())
        self.assertEqual(
            [cue.text for cue in result.cues],
            ["清爽开场", "年轻有活力"],
        )
        self.assertLess(result.cues[0].start_seconds, result.cues[1].start_seconds)

    def test_06_overlapping_global_cues_are_rejected(self) -> None:
        request = self._request()
        settings = deepcopy(dict(request.settings))
        settings["global_timeline"]["subtitle_cues"][1]["start"] = 2.0
        invalid = type(request)(
            script=request.script,
            audio_duration_seconds=request.audio_duration_seconds,
            language=request.language,
            output_format=request.output_format,
            settings=settings,
        )
        with self.assertRaisesRegex(SubtitleProviderError, "重叠"):
            StoryboardSubtitleProvider().generate_subtitle(invalid)

    def test_07_forbidden_subtitle_window_is_rejected(self) -> None:
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "av_timeline_constraints": {
                    "forbidden_windows": [
                        {"start": 1.2, "end": 1.5, "tracks": ["subtitle"]}
                    ]
                }
            },
        )
        with self.assertRaisesRegex(SubtitleProviderError, "禁用窗口"):
            self.registry.generate_subtitle(self._request())

    def test_07b_invalid_text_range_and_total_duration_are_rejected(self) -> None:
        request = self._request()
        cases = (
            ("text", "", "text 不能为空"),
            ("end", 1.0, "start < end"),
            ("end", 13.0, "超过视频总时长"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                settings = deepcopy(dict(request.settings))
                settings["global_timeline"]["subtitle_cues"][0][field] = value
                invalid = type(request)(
                    script=request.script,
                    audio_duration_seconds=request.audio_duration_seconds,
                    language=request.language,
                    output_format=request.output_format,
                    settings=settings,
                )
                with self.assertRaisesRegex(SubtitleProviderError, message):
                    StoryboardSubtitleProvider().generate_subtitle(invalid)

    def test_08_first_generation_creates_v001_with_source_config(self) -> None:
        entry = generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(entry["version"], 1)
        config = json.loads(
            self.paths.subtitle_version_config_path(1).read_text(encoding="utf-8")
        )
        self.assertEqual(config["provider"], "script_subtitle")
        self.assertEqual(config["source"], "active_voice")
        self.assertEqual(config["semantic_type"], "NARRATION_CAPTION")
        self.assertEqual(config["timing_source"], "voice_audio_duration")

    def test_09_manual_regeneration_creates_v002_without_overwrite(self) -> None:
        first = generate_subtitle_for_project(self.manager, self.registry)
        v1 = self.paths.subtitle_version_srt_path(1).read_bytes()
        second = generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(self.paths.subtitle_version_srt_path(1).read_bytes(), v1)

    def test_10_resume_keeps_existing_version_without_regeneration(self) -> None:
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Storyboard Subtitle",
            {"product_name": "P", "product_description": "D", "user_notes": ""},
        )
        self.paths.final_video_path().write_bytes(b"mock-video")
        checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "total_duration": 12.0,
            }
        )
        checkpoint.save()
        logger = TaskLogger(self.paths, "storyboard-subtitle")
        post_production_menu(
            self.paths,
            checkpoint,
            logger,
            input_fn=Inputs(["2", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(len(self.manager.load_manifest()["versions"]), 1)
        post_production_menu(
            self.paths,
            ProjectCheckpoint.load(self.paths),
            logger,
            input_fn=Inputs(["2", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(len(self.manager.load_manifest()["versions"]), 1)

    def test_11_old_project_without_compiled_storyboard_still_works(self) -> None:
        self.paths.storyboard_file_path().unlink()
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                script="兼容旧项目。", voice="local", language="zh-CN"
            ),
            LocalVoiceProvider(),
        )
        entry = generate_subtitle_for_project(self.manager, self.registry)
        self.assertIsInstance(
            self.registry.resolve(
                load_storyboard_subtitle_source(self.paths).request
                if load_storyboard_subtitle_source(self.paths)
                else self._voice_request()
            ),
            ScriptSubtitleProvider,
        )
        self.assertEqual(entry["provider"], "script_subtitle")

    def _voice_request(self):
        return load_active_voice_subtitle_source(self.paths).request

    def test_12_voice_assets_are_not_modified(self) -> None:
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                script="独立配音。", voice="local", language="zh-CN"
            ),
            LocalVoiceProvider(),
        )
        before = self.paths.voice_manifest_path().read_bytes()
        generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(self.paths.voice_manifest_path().read_bytes(), before)

    def test_13_music_assets_are_not_modified(self) -> None:
        self.paths.music_manifest_path().write_text(
            '{"active_version":1,"versions":[]}', encoding="utf-8"
        )
        before = self.paths.music_manifest_path().read_bytes()
        generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(self.paths.music_manifest_path().read_bytes(), before)

    def test_14_export_assets_are_not_modified(self) -> None:
        self.paths.export_manifest_path().write_text(
            '{"active_version":1,"versions":[]}', encoding="utf-8"
        )
        before = self.paths.export_manifest_path().read_bytes()
        generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(self.paths.export_manifest_path().read_bytes(), before)

    def test_15_active_voice_subtitle_generation_never_uses_network(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("network used")):
            entry = generate_subtitle_for_project(self.manager, self.registry)
        self.assertEqual(entry["provider"], "script_subtitle")


if __name__ == "__main__":
    unittest.main()
