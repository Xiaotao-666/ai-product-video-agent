from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from audio_timeline_calibration import (
    CALIBRATION_NOT_APPLICABLE,
    CALIBRATION_OUT_OF_BOUNDS,
    CALIBRATION_OUT_OF_TOLERANCE,
    CALIBRATION_PASS,
    CALIBRATION_WARNING,
    calibrate_voice_timeline,
)
from post_production_menu import post_production_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)
from voice_provider_registry import VoiceProviderRegistry


def silent_wav(duration: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\x00\x00" * max(1, int(duration * rate)))
    return buffer.getvalue()


def planned(**overrides):
    values = {
        "script_source": "compiled_storyboard",
        "actual_audio_duration": 8.0,
        "planned_narration_duration": 8.0,
        "planned_voice_span": 9.0,
        "planned_first_voice_start": 2.0,
        "total_video_duration": 12.0,
        "source_storyboard_path": "storyboard/storyboard.json",
        "voice_version": 1,
        "audio_sha256": "a" * 64,
        "calibrated_at": "2026-08-17T12:00:00+08:00",
    }
    values.update(overrides)
    return calibrate_voice_timeline(**values)


class SequenceVoiceProvider(VoiceProvider):
    provider_name = "mock_voice"
    model_name = "mock-v1"
    api_version = "mock"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self, durations: list[float]) -> None:
        self.durations = list(durations)
        self.calls = 0

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        duration = self.durations[min(self.calls, len(self.durations) - 1)]
        self.calls += 1
        return VoiceGenerationResult(
            audio_bytes=silent_wav(duration),
            duration_seconds=999.0,
            provider_task_id=f"mock-{self.calls}",
        )


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class AudioTimelineCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _save_voice(
        self,
        duration: float,
        *,
        script_source: str = "compiled_storyboard",
        planned_duration: float | None = 8.0,
    ) -> tuple[dict, SequenceVoiceProvider]:
        provider = SequenceVoiceProvider([duration])
        settings = {
            "script_source": script_source,
            "source_storyboard_path": "storyboard/storyboard.json",
            "planned_narration_duration": planned_duration,
            "planned_first_voice_start": 2.0,
            "planned_last_voice_end": 11.0,
            "planned_voice_span": 9.0,
            "total_video_duration": 12.0,
            "cue_count": 2,
        }
        entry = VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                "第一句。\n第二句。",
                "xiaoyan",
                "zh-CN",
                settings=settings,
            ),
            provider,
        )
        return entry, provider

    def _menu_project(self, duration: float):
        self.paths.save_json(
            self.paths.storyboard_file_path(),
            {
                "total_duration": 12,
                "shots": [
                    {
                        "shot_id": 1,
                        "duration": 6,
                        "purpose": "产品",
                        "visual": "产品特写",
                        "camera": "缓慢推进",
                        "voiceover_cues": [
                            {
                                "text": "清新产品，活力登场。",
                                "start_offset": 2.0,
                                "end_offset": 6.0,
                            }
                        ],
                        "subtitle_cues": [],
                        "video_constraints": {
                            "reserve_subtitle_space": False,
                            "subtitle_safe_area": "none",
                        },
                    },
                    {
                        "shot_id": 2,
                        "duration": 6,
                        "purpose": "品牌收束",
                        "visual": "包装定格",
                        "camera": "固定镜头",
                        "voiceover_cues": [
                            {
                                "text": "清爽延续。",
                                "start_offset": 0.0,
                                "end_offset": 4.0,
                            }
                        ],
                        "subtitle_cues": [],
                        "video_constraints": {
                            "reserve_subtitle_space": False,
                            "subtitle_safe_area": "none",
                        },
                    },
                ],
            },
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            {
                "narration_plan": {
                    "enabled": True,
                    "tone": "清新",
                    "full_script": "清新产品，活力登场。",
                    "target_duration_seconds": 8.0,
                }
            },
        )
        checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Calibration",
            {
                "product_name": "产品",
                "product_description": "描述",
                "user_notes": "",
            },
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
        provider = SequenceVoiceProvider([duration, duration])
        registry = VoiceProviderRegistry({"default_provider": "mock_voice"})
        registry.register(provider)
        return checkpoint, provider, registry

    def test_01_equal_duration_is_pass(self):
        self.assertEqual(planned()["status"], CALIBRATION_PASS)

    def test_02_seven_point_four_seconds_is_pass(self):
        result = planned(actual_audio_duration=7.4)
        self.assertEqual(result["status"], CALIBRATION_PASS)
        self.assertEqual(result["duration_difference_ratio"], -0.075)

    def test_03_fifteen_percent_difference_is_warning(self):
        self.assertEqual(
            planned(actual_audio_duration=6.8)["status"], CALIBRATION_WARNING
        )

    def test_04_over_twenty_percent_is_out_of_tolerance(self):
        self.assertEqual(
            planned(actual_audio_duration=5.8)["status"],
            CALIBRATION_OUT_OF_TOLERANCE,
        )

    def test_05_actual_voice_end_is_start_plus_audio(self):
        result = planned(actual_audio_duration=7.0)
        self.assertEqual(result["actual_voice_end"], 9.0)

    def test_06_forbidden_lead_in_keeps_two_second_start(self):
        self.assertEqual(planned()["voice_track_start"], 2.0)

    def test_07_end_past_video_is_out_of_bounds(self):
        result = planned(
            planned_first_voice_start=5.0,
            actual_audio_duration=9.0,
            total_video_duration=12.0,
        )
        self.assertEqual(result["actual_voice_end"], 14.0)
        self.assertEqual(result["status"], CALIBRATION_OUT_OF_BOUNDS)

    def test_08_narration_duration_and_voice_span_remain_distinct(self):
        result = planned(actual_audio_duration=8.0, planned_voice_span=11.0)
        self.assertEqual(result["planned_narration_duration"], 8.0)
        self.assertEqual(result["planned_voice_span"], 11.0)

    def test_09_span_with_silence_does_not_affect_status(self):
        result = planned(actual_audio_duration=7.4, planned_voice_span=30.0)
        self.assertEqual(result["status"], CALIBRATION_PASS)

    def test_10_warning_does_not_auto_regenerate(self):
        checkpoint, provider, registry = self._menu_project(6.8)
        post_production_menu(
            self.paths,
            checkpoint,
            TaskLogger(self.paths, "warning"),
            voice_registry=registry,
            input_fn=Inputs(["1", "", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(provider.calls, 1)

    def test_11_out_of_tolerance_does_not_auto_regenerate(self):
        checkpoint, provider, registry = self._menu_project(5.8)
        post_production_menu(
            self.paths,
            checkpoint,
            TaskLogger(self.paths, "out"),
            voice_registry=registry,
            input_fn=Inputs(["1", "", "1", "4", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(provider.calls, 1)

    def test_12_explicit_regenerate_is_the_only_second_provider_call(self):
        checkpoint, provider, registry = self._menu_project(5.8)
        post_production_menu(
            self.paths,
            checkpoint,
            TaskLogger(self.paths, "explicit"),
            voice_registry=registry,
            input_fn=Inputs(["1", "", "1", "2", "1", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(VoiceAssetManager(self.paths).load_manifest()["active_version"], 2)

    def test_13_calibration_metadata_is_saved_in_config_and_manifest(self):
        entry, _ = self._save_voice(7.4)
        calibration = entry["timing_calibration"]
        required = {
            "timing_mode",
            "status",
            "planned_narration_duration",
            "planned_voice_span",
            "actual_audio_duration",
            "voice_track_start",
            "actual_voice_end",
            "duration_difference_seconds",
            "duration_difference_ratio",
            "cue_level_alignment",
            "calibrated_at",
            "source_fingerprint",
        }
        self.assertTrue(required.issubset(calibration))
        config = json.loads(
            self.paths.voice_version_config_path(1).read_text(encoding="utf-8")
        )
        self.assertEqual(config["timing_calibration"], calibration)

    def test_14_audio_sha256_is_recorded_in_source_fingerprint(self):
        entry, _ = self._save_voice(7.4)
        expected = hashlib.sha256(
            self.paths.voice_version_audio_path(1).read_bytes()
        ).hexdigest()
        self.assertEqual(entry["audio_sha256"], expected)
        self.assertEqual(
            entry["timing_calibration"]["source_fingerprint"]["audio_sha256"],
            expected,
        )

    def test_15_resume_reads_existing_calibration_without_provider(self):
        entry, provider = self._save_voice(7.4)
        before = json.loads(json.dumps(entry["timing_calibration"]))
        resumed = VoiceAssetManager(
            create_project_paths(self.paths.project_path)
        ).active_version()
        self.assertEqual(resumed["timing_calibration"], before)
        self.assertEqual(provider.calls, 1)

    def test_16_manual_voice_is_not_applicable_and_starts_at_zero(self):
        entry, _ = self._save_voice(
            1.0, script_source="manual", planned_duration=None
        )
        calibration = entry["timing_calibration"]
        self.assertEqual(calibration["status"], CALIBRATION_NOT_APPLICABLE)
        self.assertEqual(calibration["voice_track_start"], 0.0)

    def test_17_storyboard_edited_records_script_mismatch(self):
        entry, _ = self._save_voice(7.4, script_source="storyboard_edited")
        self.assertFalse(
            entry["timing_calibration"]["script_matches_storyboard"]
        )

    def test_18_legacy_voice_without_calibration_still_loads(self):
        self._save_voice(7.4)
        manifest = VoiceAssetManager(self.paths).load_manifest()
        manifest["versions"][0].pop("timing_calibration")
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        self.assertNotIn(
            "timing_calibration", VoiceAssetManager(self.paths).active_version()
        )

    def _assert_source_unchanged(self, filename: str):
        path = Path(__file__).resolve().parent.parent / filename
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        planned(actual_audio_duration=7.4)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_19_subtitle_pipeline_source_is_unchanged(self):
        self._assert_source_unchanged("subtitle_generation.py")

    def test_20_music_pipeline_source_is_unchanged(self):
        self._assert_source_unchanged("music_generation.py")

    def test_21_final_export_core_is_unchanged(self):
        self._assert_source_unchanged("export_pipeline.py")

    def test_22_timeline_scheduler_core_is_unchanged(self):
        self._assert_source_unchanged("timeline_scheduler.py")

    def test_23_calibration_and_mock_voice_use_no_network(self):
        with patch("socket.socket", side_effect=AssertionError("network used")):
            entry, provider = self._save_voice(7.4)
        self.assertEqual(entry["timing_calibration"]["status"], CALIBRATION_PASS)
        self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
