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
from storyboard import Storyboard
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_generation import generate_confirmed_voice
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
    VoiceProviderError,
)
from voice_provider_registry import VoiceProviderRegistry
from voice_script_builder import (
    build_voice_script_from_storyboard,
    load_storyboard_voice_script,
)


def silent_wav(duration_seconds: float = 0.2, sample_rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(
            b"\x00\x00" * max(1, int(duration_seconds * sample_rate))
        )
    return buffer.getvalue()


def compiled_storyboard(*, with_voice: bool = True) -> dict:
    first_voice = (
        [{"text": "每一颗柠檬，都带着阳光。", "start_offset": 2.0, "end_offset": 4.0}]
        if with_voice
        else []
    )
    second_voice = (
        [{"text": "LEE柠檬，新鲜每一刻。", "start_offset": 1.0, "end_offset": 3.0}]
        if with_voice
        else []
    )
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "产品亮相",
                "visual": "柠檬产品特写",
                "camera": "缓慢推进",
                "voiceover_cues": first_voice,
                "subtitle_cues": [
                    {
                        "text": "新鲜",
                        "start_offset": 1.0,
                        "end_offset": 2.0,
                        "position": "bottom_center",
                    }
                ],
                "video_constraints": {
                    "reserve_subtitle_space": True,
                    "subtitle_safe_area": "bottom_center",
                },
            },
            {
                "shot_id": 2,
                "duration": 6,
                "purpose": "品牌收束",
                "visual": "包装稳定居中",
                "camera": "固定镜头",
                "voiceover_cues": second_voice,
                "subtitle_cues": [
                    {
                        "text": "活力",
                        "start_offset": 0.2,
                        "end_offset": 0.8,
                        "position": "top_center",
                    }
                ],
                "video_constraints": {
                    "reserve_subtitle_space": True,
                    "subtitle_safe_area": "top_center",
                },
            },
        ],
    }


class MockVoiceProvider(VoiceProvider):
    provider_name = "mock_voice"
    model_name = "mock-v1"
    api_version = "mock"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self) -> None:
        self.calls = 0
        self.scripts: list[str] = []

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        self.scripts.append(request.script)
        return VoiceGenerationResult(
            audio_bytes=silent_wav(),
            duration_seconds=99.0,
            provider_task_id=f"mock-{self.calls}",
        )


class FailingVoiceProvider(MockVoiceProvider):
    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        raise VoiceProviderError("mock TTS failed")


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class StoryboardVoiceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.paths.save_json(
            self.paths.storyboard_file_path(), compiled_storyboard()
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "narration_plan": {
                    "enabled": True,
                    "tone": "清新",
                    "full_script": "每一颗柠檬，都带着阳光。LEE柠檬，新鲜每一刻。",
                    "target_duration_seconds": 8.0,
                }
            },
        )
        self.provider = MockVoiceProvider()
        self.registry = VoiceProviderRegistry({"default_provider": "mock_voice"})
        self.registry.register(self.provider)
        self.manager = VoiceAssetManager(self.paths)
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Storyboard Voice",
            {"product_name": "柠檬", "product_description": "新鲜", "user_notes": ""},
        )
        self.paths.final_video_path().write_bytes(b"mock-video")
        self.checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "total_duration": 12.0,
            }
        )
        self.checkpoint.save()
        self.logger = TaskLogger(self.paths, "storyboard-voice")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self):
        plan = load_storyboard_voice_script(self.paths)
        self.assertIsNotNone(plan)
        return plan

    def _request(self) -> VoiceGenerationRequest:
        plan = self._plan()
        return VoiceGenerationRequest(
            script=plan.script,
            voice="xiaoyan",
            language="zh-CN",
            settings=plan.request_settings(),
        )

    def _generate(self, inputs: list[str] | None = None) -> dict:
        entry = generate_confirmed_voice(
            self.manager,
            self.registry,
            self._request(),
            input_fn=Inputs(inputs or ["1"]),
            output_fn=lambda _value: None,
        )
        self.assertIsNotNone(entry)
        return entry

    def test_01_multi_shot_voice_cues_follow_global_time_order(self) -> None:
        plan = build_voice_script_from_storyboard(
            Storyboard.model_validate(compiled_storyboard()),
            planned_narration_duration=8.0,
        )
        self.assertEqual(
            plan.script.splitlines(),
            ["每一颗柠檬，都带着阳光。", "LEE柠檬，新鲜每一刻。"],
        )

    def test_02_builder_reads_only_voiceover_text(self) -> None:
        self.assertIn("每一颗柠檬", self._plan().script)
        self.assertIn("LEE柠檬", self._plan().script)

    def test_03_builder_never_reads_subtitle_text(self) -> None:
        self.assertNotIn("新鲜\n活力", self._plan().script)
        self.assertNotIn("\n新鲜\n", f"\n{self._plan().script}\n")
        self.assertNotIn("\n活力\n", f"\n{self._plan().script}\n")

    def test_04_builder_is_deterministic(self) -> None:
        board = Storyboard.model_validate(compiled_storyboard())
        self.assertEqual(
            build_voice_script_from_storyboard(board),
            build_voice_script_from_storyboard(board),
        )

    def test_05_empty_voice_cues_do_not_create_empty_script(self) -> None:
        board = Storyboard.model_validate(compiled_storyboard(with_voice=False))
        self.assertIsNone(build_voice_script_from_storyboard(board))

    def test_06_storyboard_voice_is_default_source(self) -> None:
        self.assertEqual(self._plan().request_settings()["script_source"], "compiled_storyboard")

    def test_07_no_storyboard_voice_falls_back_to_manual(self) -> None:
        self.paths.save_json(
            self.paths.storyboard_file_path(), compiled_storyboard(with_voice=False)
        )
        self.assertIsNone(load_storyboard_voice_script(self.paths))
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "1", "手动旁白。", "END", "", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.manager.active_version()["script_source"], "manual")

    def test_08_user_can_explicitly_switch_to_manual_script(self) -> None:
        entry = self._generate(["3", "自定义旁白。", "END", "1"])
        self.assertEqual(entry["script_source"], "manual")
        self.assertEqual(self.provider.scripts, ["自定义旁白。"])

    def test_08b_storyboard_edit_is_version_local_and_does_not_mutate_plan(self) -> None:
        before = self.paths.storyboard_file_path().read_bytes()
        entry = self._generate(["2", "本次配音专用修改。", "END", "1"])
        self.assertEqual(entry["script_source"], "storyboard_edited")
        self.assertEqual(self.provider.scripts, ["本次配音专用修改。"])
        self.assertEqual(self.paths.storyboard_file_path().read_bytes(), before)

    def test_09_full_storyboard_script_calls_tts_once(self) -> None:
        self._generate()
        self.assertEqual(self.provider.calls, 1)

    def test_10_multiple_cues_do_not_create_multiple_tts_calls(self) -> None:
        self._generate()
        self.assertEqual(self._plan().cue_count, 2)
        self.assertEqual(len(self.provider.scripts), 1)

    def test_11_tts_failure_does_not_commit_partial_voice_version(self) -> None:
        failed = FailingVoiceProvider()
        registry = VoiceProviderRegistry({"default_provider": "mock_voice"})
        registry.register(failed)
        with self.assertRaisesRegex(VoiceProviderError, "failed"):
            generate_confirmed_voice(
                self.manager,
                registry,
                self._request(),
                input_fn=Inputs(["1"]),
                output_fn=lambda _value: None,
            )
        self.assertFalse(self.paths.voice_manifest_path().exists())
        self.assertFalse(self.paths.voice_version_dir(1).exists())

    def test_12_metadata_records_planned_narration_duration(self) -> None:
        self.assertEqual(self._generate()["planned_narration_duration"], 8.0)

    def test_13_metadata_records_first_global_voice_start(self) -> None:
        self.assertEqual(self._generate()["planned_first_voice_start"], 2.0)

    def test_14_metadata_records_last_global_voice_end(self) -> None:
        self.assertEqual(self._generate()["planned_last_voice_end"], 9.0)

    def test_15_metadata_records_actual_wav_duration(self) -> None:
        entry = self._generate()
        self.assertAlmostEqual(entry["actual_audio_duration"], 0.2, places=3)
        self.assertNotEqual(entry["actual_audio_duration"], 99.0)

    def test_16_metadata_records_cue_count_and_distinct_span(self) -> None:
        entry = self._generate()
        self.assertEqual(entry["cue_count"], 2)
        self.assertEqual(entry["planned_voice_span"], 7.0)
        self.assertEqual(entry["planned_narration_duration"], 8.0)

    def test_17_resume_with_existing_voice_does_not_repeat_tts(self) -> None:
        self._generate()
        output: list[str] = []
        post_production_menu(
            self.paths,
            ProjectCheckpoint.load(self.paths),
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "1", "5"]),
            output_fn=output.append,
        )
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(len(self.manager.load_manifest()["versions"]), 1)
        rendered = "\n".join(output)
        self.assertIn("Storyboard Planned", rendered)
        self.assertIn("Planned", rendered)
        self.assertIn("Actual", rendered)
        self.assertIn("OUT_OF_TOLERANCE", rendered)

    def test_18_explicit_regeneration_creates_v002(self) -> None:
        self._generate()
        post_production_menu(
            self.paths,
            ProjectCheckpoint.load(self.paths),
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "2", "", "1", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(self.manager.load_manifest()["active_version"], 2)

    def test_19_old_project_without_storyboard_supports_manual_voice(self) -> None:
        self.paths.storyboard_file_path().unlink()
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "1", "旧项目旁白。", "END", "", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.manager.active_version()["script_source"], "manual")

    def test_20_disabled_narration_offers_manual_without_empty_audio(self) -> None:
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {"narration_plan": {"enabled": False, "target_duration_seconds": 0}},
        )
        self.assertIsNone(load_storyboard_voice_script(self.paths))
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "2", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.paths.voice_manifest_path().exists())

    def test_21_subtitle_pipeline_assets_are_unchanged(self) -> None:
        self.paths.subtitle_manifest_path().write_text(
            '{"subtitle_schema_version":1,"active_version":null,"versions":[]}',
            encoding="utf-8",
        )
        before = self.paths.subtitle_manifest_path().read_bytes()
        self._generate()
        self.assertEqual(self.paths.subtitle_manifest_path().read_bytes(), before)

    def test_22_music_pipeline_assets_are_unchanged(self) -> None:
        self.paths.music_manifest_path().write_text(
            '{"active_version":null,"versions":[]}', encoding="utf-8"
        )
        before = self.paths.music_manifest_path().read_bytes()
        self._generate()
        self.assertEqual(self.paths.music_manifest_path().read_bytes(), before)

    def test_23_final_export_assets_are_unchanged(self) -> None:
        self.paths.export_manifest_path().write_text(
            '{"active_version":null,"versions":[]}', encoding="utf-8"
        )
        before = self.paths.export_manifest_path().read_bytes()
        self._generate()
        self.assertEqual(self.paths.export_manifest_path().read_bytes(), before)

    def test_24_shot_schema_state_is_unchanged(self) -> None:
        before = deepcopy(self.checkpoint.data["video_generation"])
        self._generate()
        self.assertEqual(self.checkpoint.data["video_generation"], before)

    def test_25_no_real_api_or_network_call(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("network used")):
            self._generate()
        self.assertEqual(self.provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
