from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from export_assets import ExportAssetManager
from export_pipeline import ExportAlreadyExistsError, ExportPipeline, ExportPipelineError
from music_assets import MusicAssetManager
from music_generation import add_local_music
from music_mix import (
    MusicMixError,
    MusicMixSettingsManager,
    build_music_mix_plan,
    music_ducking_expression,
)
from music_provider_registry import build_music_provider_registry
from post_production_menu import project_resume_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from task_logger import TaskLogger
from tests.test_timeline_aware_export import CountingVoiceProvider, Inputs, silent_wav
from voice_assets import VoiceAssetManager
from voice_provider import VoiceGenerationRequest


class MusicTimelineRunner:
    def __init__(
        self,
        *,
        video_duration: float = 12.0,
        music_duration: float = 12.0,
        voice_duration: float = 7.4,
        ffmpeg_failure: bool = False,
        final_probe_failure: bool = False,
    ) -> None:
        self.video_duration = video_duration
        self.music_duration = music_duration
        self.voice_duration = voice_duration
        self.ffmpeg_failure = ffmpeg_failure
        self.final_probe_failure = final_probe_failure
        self.commands: list[list[str]] = []
        self.ffmpeg_calls = 0

    def __call__(self, command: list[str], **_kwargs):
        self.commands.append(list(command))
        executable = Path(command[0]).name.lower()
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "mock 9.0\n", "")
        if "ffprobe" in executable:
            target = Path(command[-1])
            if (
                self.final_probe_failure
                and target.name == "final_video.mp4"
                and ".staging" in target.as_posix()
            ):
                return subprocess.CompletedProcess(command, 1, "", "broken final")
            normalized = target.as_posix().lower()
            if "/music/" in normalized:
                duration = self.music_duration
                streams = [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}]
            elif target.name == "audio.wav":
                duration = self.voice_duration
                streams = [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}]
            else:
                duration = self.video_duration
                streams = [
                    {"index": 0, "codec_type": "video", "codec_name": "h264"},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                ]
            payload = json.dumps(
                {"format": {"duration": str(duration)}, "streams": streams}
            )
            return subprocess.CompletedProcess(command, 0, payload, "")
        if "ffmpeg" in executable:
            self.ffmpeg_calls += 1
            if self.ffmpeg_failure:
                return subprocess.CompletedProcess(command, 1, "", "duck failure")
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(f"music-export-{self.ffmpeg_calls}".encode())
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unknown")


class MusicTimelineDuckingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = create_project_paths(root / "project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Music Timeline",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        self.paths.final_video_path().write_bytes(b"assembled-video")
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
        self.voice_provider = CountingVoiceProvider()
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                "第一句。\n第二句。",
                "timeline",
                "zh-CN",
                settings={
                    "script_source": "compiled_storyboard",
                    "planned_narration_duration": 8.0,
                    "planned_first_voice_start": 2.0,
                    "planned_last_voice_end": 11.0,
                    "planned_voice_span": 9.0,
                    "total_video_duration": 12.0,
                    "cue_count": 2,
                },
            ),
            self.voice_provider,
        )
        self.music_source = root / "music.wav"
        self.music_source.write_bytes(silent_wav(12.0))
        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_source,
            music_volume=0.25,
        )
        self.runner = MusicTimelineRunner()
        self.logger = TaskLogger(self.paths, "music-timeline")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pipeline(self, runner: MusicTimelineRunner | None = None) -> ExportPipeline:
        return ExportPipeline(
            self.paths,
            self.checkpoint,
            self.logger,
            runner=runner or self.runner,
            which=lambda name: name,
        )

    def mix_manager(self) -> MusicMixSettingsManager:
        return MusicMixSettingsManager(self.checkpoint)

    def set_voice_timing(self, **updates) -> None:
        manager = VoiceAssetManager(self.paths)
        manifest = manager.load_manifest()
        manifest["versions"][0]["timing_calibration"].update(updates)
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)

    def render_inputs(self, pipeline: ExportPipeline | None = None):
        selected = pipeline or self.pipeline()
        return selected._resolve_music_mix_for_fingerprint(selected.collect_inputs())

    def graph(self, runner: MusicTimelineRunner | None = None) -> str:
        selected = runner or self.runner
        command = next(
            item
            for item in selected.commands
            if "ffmpeg" in Path(item[0]).name.lower() and "-version" not in item
        )
        return command[command.index("-filter_complex") + 1]

    # Ducking
    def test_01_voice_window_is_two_to_nine_point_four(self):
        plan = self.render_inputs().music_mix
        self.assertEqual((plan["ducking_start"], plan["ducking_end"]), (2.0, 9.4))
        self.pipeline().export_current()
        graph = self.graph()
        self.assertIn("t-1.75", graph)
        self.assertIn("lt(t,9.4)", graph)
        self.assertIn("t-9.4", graph)

    def test_02_base_and_ratio_produce_point_one(self):
        self.assertAlmostEqual(self.render_inputs().music_mix["ducked_volume"], 0.10)

    def test_03_attack_begins_before_voice(self):
        plan = self.render_inputs().music_mix
        self.assertEqual(plan["attack_start"], 1.75)
        self.assertEqual(plan["duck_attack_seconds"], 0.25)

    def test_04_release_ends_after_voice(self):
        plan = self.render_inputs().music_mix
        self.assertEqual(plan["release_end"], 9.75)
        self.assertEqual(plan["duck_release_seconds"], 0.35)

    def test_05_disabled_ducking_keeps_base_volume(self):
        self.mix_manager().update(ducking_enabled=False)
        plan = self.render_inputs().music_mix
        self.assertFalse(plan["ducking_enabled"])
        self.assertIsNone(music_ducking_expression(plan))

    def test_06_no_voice_disables_ducking(self):
        self.paths.voice_manifest_path().unlink()
        plan = self.render_inputs().music_mix
        self.assertFalse(plan["ducking_enabled"])
        self.assertEqual(plan["ducking_status"], "NO_VOICE")

    # Boundaries
    def test_07_voice_start_zero_clamps_attack(self):
        self.set_voice_timing(voice_track_start=0.0, actual_voice_end=7.4)
        plan = self.render_inputs().music_mix
        self.assertEqual(plan["attack_start"], 0.0)
        self.assertEqual(plan["duck_attack_seconds"], 0.0)

    def test_08_voice_end_at_video_end_clamps_release(self):
        self.set_voice_timing(actual_voice_end=12.0)
        plan = self.render_inputs().music_mix
        self.assertEqual(plan["release_end"], 12.0)
        self.assertEqual(plan["duck_release_seconds"], 0.0)

    def test_09_negative_attack_or_release_is_rejected(self):
        with self.assertRaises(MusicMixError):
            self.mix_manager().update(duck_attack_seconds=-0.1)
        with self.assertRaises(MusicMixError):
            self.mix_manager().update(duck_release_seconds=-0.1)

    # Fade
    def test_10_fade_in_filter_is_present(self):
        self.pipeline().export_current()
        self.assertIn("afade=t=in:st=0:d=0.8", self.graph())

    def test_11_fade_out_filter_is_present(self):
        self.pipeline().export_current()
        self.assertIn("afade=t=out:st=10.8:d=1.2", self.graph())

    def test_12_zero_fades_are_valid(self):
        self.mix_manager().update(fade_in_seconds=0, fade_out_seconds=0)
        self.pipeline().export_current()
        self.assertNotIn("afade=", self.graph())

    def test_13_short_music_clamps_overlapping_fades(self):
        plan = build_music_mix_plan(
            self.mix_manager().current(),
            original_music_duration=1.0,
            video_duration=12.0,
            voice_timing=None,
        )
        self.assertAlmostEqual(plan["fade_in_seconds"], 0.4)
        self.assertAlmostEqual(plan["fade_out_seconds"], 0.6)
        self.assertLessEqual(plan["fade_in_seconds"] + plan["fade_out_seconds"], 1.0)

    # Music duration
    def test_14_long_music_is_trimmed_to_video(self):
        runner = MusicTimelineRunner(music_duration=40.0)
        self.pipeline(runner).export_current()
        self.assertIn("atrim=start=0:end=12", self.graph(runner))

    def test_15_equal_music_duration_is_supported(self):
        self.assertEqual(self.render_inputs().music_mix["effective_music_end"], 12.0)

    def test_16_short_music_is_not_looped(self):
        runner = MusicTimelineRunner(music_duration=8.0)
        self.pipeline(runner).export_current()
        command = next(item for item in runner.commands if "ffmpeg" in Path(item[0]).name.lower() and "-version" not in item)
        self.assertNotIn("-stream_loop", command)

    def test_17_short_music_is_padded_with_silence(self):
        runner = MusicTimelineRunner(music_duration=8.0)
        self.pipeline(runner).export_current()
        self.assertIn("apad=whole_dur=12", self.graph(runner))
        manifest = json.loads(self.paths.export_version_manifest_path(1).read_text(encoding="utf-8"))
        self.assertTrue(manifest["music_mix"]["padded_with_silence"])

    def test_18_video_controls_final_duration(self):
        runner = MusicTimelineRunner(video_duration=12.0, music_duration=8.0)
        self.pipeline(runner).export_current()
        manifest = json.loads(self.paths.export_version_manifest_path(1).read_text(encoding="utf-8"))
        self.assertEqual(manifest["duration_seconds"], 12.0)

    # Asset integrity
    def test_19_original_music_sha_is_unchanged(self):
        path = self.paths.music_version_audio_path(1, "wav")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.pipeline().export_current()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_20_ducking_change_does_not_create_music_version(self):
        before = len(MusicAssetManager(self.paths).load_manifest()["versions"])
        self.mix_manager().update(ducking_ratio=0.6)
        self.assertEqual(len(MusicAssetManager(self.paths).load_manifest()["versions"]), before)

    def test_21_fade_change_does_not_create_music_version(self):
        before = len(MusicAssetManager(self.paths).load_manifest()["versions"])
        self.mix_manager().update(fade_in_seconds=0.2)
        self.assertEqual(len(MusicAssetManager(self.paths).load_manifest()["versions"]), before)

    def test_22_replacing_music_creates_v002(self):
        replacement = Path(self.temp.name) / "replacement.wav"
        replacement.write_bytes(silent_wav(5.0))
        entry = add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            replacement,
        )
        self.assertEqual(entry["version"], 2)

    # Fingerprint
    def fingerprint(self) -> dict:
        pipeline = self.pipeline()
        return pipeline.build_input_fingerprint(self.render_inputs(pipeline))

    def test_23_ratio_changes_fingerprint(self):
        first = self.fingerprint()
        self.mix_manager().update(ducking_ratio=0.6)
        self.assertNotEqual(first, self.fingerprint())

    def test_24_fade_changes_fingerprint(self):
        first = self.fingerprint()
        self.mix_manager().update(fade_out_seconds=0.5)
        self.assertNotEqual(first, self.fingerprint())

    def test_25_base_volume_changes_fingerprint(self):
        first = self.fingerprint()
        self.mix_manager().update(base_volume=0.2)
        self.assertNotEqual(first, self.fingerprint())

    def test_26_voice_start_still_changes_fingerprint(self):
        first = self.fingerprint()
        self.set_voice_timing(voice_track_start=1.0, actual_voice_end=8.4)
        self.assertNotEqual(first, self.fingerprint())

    def test_27_identical_mix_hits_duplicate_export(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        self.assertIsNotNone(pipeline.find_existing_export())
        with self.assertRaises(ExportAlreadyExistsError):
            pipeline.export_current()

    # Manifest and Resume
    def test_28_manifest_saves_complete_music_mix(self):
        self.pipeline().export_current()
        payload = json.loads(self.paths.export_version_manifest_path(1).read_text(encoding="utf-8"))
        mix = payload["music_mix"]
        for field in (
            "music_version", "original_duration", "effective_music_end",
            "base_volume", "ducking_enabled", "ducking_ratio", "ducked_volume",
            "ducking_start", "ducking_end", "duck_attack_seconds",
            "duck_release_seconds", "fade_in_seconds", "fade_out_seconds",
            "loop_music", "padded_with_silence",
        ):
            self.assertIn(field, mix)

    def test_29_resume_displays_music_mix(self):
        self.pipeline().export_current()
        output: list[str] = []
        project_resume_menu(
            self.paths,
            ProjectCheckpoint.load(self.paths),
            self.logger,
            regenerate_assembly=lambda: None,
            open_shot_management=lambda: None,
            input_fn=Inputs(["5"]),
            output_fn=output.append,
        )
        rendered = "\n".join(output)
        self.assertIn("Ducking：Enabled", rendered)
        self.assertIn("Base / Ducked：25% / 10%", rendered)

    def test_30_resume_does_not_run_ffmpeg(self):
        self.pipeline().export_current()
        before = self.runner.ffmpeg_calls
        project_resume_menu(
            self.paths,
            ProjectCheckpoint.load(self.paths),
            self.logger,
            regenerate_assembly=lambda: None,
            open_shot_management=lambda: None,
            input_fn=Inputs(["5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.runner.ffmpeg_calls, before)

    # Compatibility
    def test_31_no_music_still_exports(self):
        self.paths.music_manifest_path().unlink()
        entry = self.pipeline().export_current()
        self.assertEqual(entry["version"], 1)

    def test_32_legacy_music_uses_default_mix(self):
        self.checkpoint.data["post_production"].pop("music_mix", None)
        self.checkpoint.save()
        plan = self.render_inputs().music_mix
        self.assertEqual(plan["base_volume"], 0.25)
        self.assertTrue(plan["ducking_requested"])

    def test_33_legacy_voice_disables_ducking_without_error(self):
        manager = VoiceAssetManager(self.paths)
        manifest = manager.load_manifest()
        manifest["versions"][0].pop("timing_calibration", None)
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        config = json.loads(self.paths.voice_version_config_path(1).read_text(encoding="utf-8"))
        config.pop("timing_calibration", None)
        self.paths.save_json(self.paths.voice_version_config_path(1), config)
        plan = self.render_inputs().music_mix
        self.assertFalse(plan["ducking_enabled"])
        self.assertEqual(plan["ducking_status"], "UNAVAILABLE_LEGACY_VOICE_TIMING")

    # Failure safety
    def test_34_ffmpeg_duck_failure_does_not_commit(self):
        runner = MusicTimelineRunner(ffmpeg_failure=True)
        with self.assertRaisesRegex(ExportPipelineError, "FFmpeg"):
            self.pipeline(runner).export_current()
        self.assertFalse(self.paths.export_version_dir(1).exists())
        self.assertIsNone(ExportAssetManager(self.paths).active_version())

    def test_35_ffprobe_failure_does_not_commit_success(self):
        runner = MusicTimelineRunner(final_probe_failure=True)
        with self.assertRaisesRegex(ExportPipelineError, "ffprobe"):
            self.pipeline(runner).export_current()
        self.assertFalse(self.paths.export_version_dir(1).exists())

    # Module isolation
    def test_36_export_does_not_call_tts(self):
        calls = self.voice_provider.calls
        self.pipeline().export_current()
        self.assertEqual(self.voice_provider.calls, calls)

    def test_37_export_does_not_call_deepseek(self):
        with patch("socket.socket", side_effect=AssertionError("network used")):
            self.pipeline().export_current()

    def test_38_export_does_not_call_minimax(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("API used")):
            self.pipeline().export_current()

    def assert_source_unchanged(self, filename: str) -> None:
        path = Path(__file__).resolve().parent.parent / filename
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.pipeline().collect_inputs()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_39_timeline_scheduler_is_unchanged(self):
        self.assert_source_unchanged("timeline_scheduler.py")

    def test_40_subtitle_pipeline_is_unchanged(self):
        self.assert_source_unchanged("subtitle_generation.py")

    def test_41_voice_pipeline_is_unchanged(self):
        self.assert_source_unchanged("voice_generation.py")

    def test_42_shot_schema_two_is_unchanged(self):
        self.assert_source_unchanged("shot_storage.py")


if __name__ == "__main__":
    unittest.main()
