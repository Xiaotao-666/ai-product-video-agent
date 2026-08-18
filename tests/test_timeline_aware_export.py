from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from export_assets import ExportAssetManager
from export_pipeline import (
    ExportAlreadyExistsError,
    ExportPipeline,
    ExportPipelineError,
)
from music_assets import MusicAssetManager
from music_generation import add_local_music
from music_provider_registry import build_music_provider_registry
from post_production import PostProductionPipeline
from post_production_menu import post_production_menu, project_resume_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from subtitle_assets import SubtitleAssetManager
from subtitle_provider import (
    SubtitleCue,
    SubtitleGenerationRequest,
    SubtitleGenerationResult,
)
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)


def silent_wav(duration: float, rate: int = 8000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(
            b"\x00\x00" * channels * max(1, int(duration * rate))
        )
    return buffer.getvalue()


class CountingVoiceProvider(VoiceProvider):
    provider_name = "timeline_mock_voice"
    model_name = "timeline-local"
    api_version = "local"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(self, duration: float = 7.4, channels: int = 1) -> None:
        self.duration = duration
        self.channels = channels
        self.calls = 0

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        return VoiceGenerationResult(
            audio_bytes=silent_wav(self.duration, channels=self.channels),
            duration_seconds=999.0,
            provider_task_id=f"voice-{self.calls}",
        )


class TimelineMediaRunner:
    def __init__(
        self,
        *,
        video_duration: float = 12.0,
        ffmpeg_failure: bool = False,
        final_probe_failure: bool = False,
    ) -> None:
        self.video_duration = video_duration
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
            if self.final_probe_failure and target.name == "final_video.mp4" and (
                ".staging" in target.as_posix()
            ):
                return subprocess.CompletedProcess(command, 1, "", "broken final")
            if target.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg"}:
                streams = [
                    {"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}
                ]
                duration = 7.4 if target.name == "audio.wav" else 12.0
            else:
                streams = [
                    {"index": 0, "codec_type": "video", "codec_name": "h264"},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                ]
                duration = self.video_duration
            payload = json.dumps(
                {"format": {"duration": str(duration)}, "streams": streams}
            )
            return subprocess.CompletedProcess(command, 0, payload, "")
        if "ffmpeg" in executable:
            self.ffmpeg_calls += 1
            if self.ffmpeg_failure:
                return subprocess.CompletedProcess(command, 1, "", "mock failure")
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(f"timeline-export-{self.ffmpeg_calls}".encode())
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unknown")


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class TimelineAwareExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = create_project_paths(root / "project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Timeline Export",
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
        self.provider = CountingVoiceProvider()
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                "第一句。\n第二句。",
                "timeline",
                "zh-CN",
                settings={
                    "script_source": "compiled_storyboard",
                    "source_storyboard_path": "storyboard/storyboard.json",
                    "planned_narration_duration": 8.0,
                    "planned_first_voice_start": 2.0,
                    "planned_last_voice_end": 11.0,
                    "planned_voice_span": 9.0,
                    "total_video_duration": 12.0,
                    "cue_count": 2,
                },
            ),
            self.provider,
        )
        self.srt_text = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一条全局字幕\n\n"
            "2\n00:00:08,000 --> 00:00:09,000\n第二条全局字幕\n"
        )
        SubtitleAssetManager(self.paths).save_result(
            SubtitleGenerationRequest("第一条全局字幕\n第二条全局字幕", 12.0),
            SubtitleGenerationResult(
                subtitle_text=self.srt_text,
                cues=(
                    SubtitleCue(1, 1.0, 2.0, "第一条全局字幕"),
                    SubtitleCue(2, 8.0, 9.0, "第二条全局字幕"),
                ),
                duration_seconds=12.0,
                metadata={
                    "source": "compiled_storyboard",
                    "timing_source": "global_timeline",
                },
            ),
            {"provider": "storyboard_subtitle", "model": "local", "api_version": "1"},
            source_voice_version=1,
            source_script_path=self.paths.voice_version_script_path(1),
            source_audio_path=self.paths.voice_version_audio_path(1),
            source_storyboard_path=None,
        )
        self.music_source = root / "music.wav"
        self.music_source.write_bytes(silent_wav(2.0))
        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_source,
            music_volume=0.25,
        )
        self.runner = TimelineMediaRunner()
        self.logger = TaskLogger(self.paths, "timeline-export")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pipeline(self, runner: TimelineMediaRunner | None = None) -> ExportPipeline:
        return ExportPipeline(
            self.paths,
            self.checkpoint,
            self.logger,
            runner=runner or self.runner,
            which=lambda name: name,
        )

    def _voice_manifest(self) -> dict:
        return VoiceAssetManager(self.paths).load_manifest()

    def _set_timing(self, **updates) -> None:
        manifest = self._voice_manifest()
        manifest["versions"][0]["timing_calibration"].update(updates)
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)

    def _set_acceptance(self, accepted: bool = True) -> None:
        VoiceAssetManager(self.paths).set_timing_acceptance(
            1, accepted=accepted, accepted_at="2026-08-17T18:00:00+08:00"
        )

    def _ffmpeg_command(self) -> list[str]:
        return next(
            command
            for command in self.runner.commands
            if "ffmpeg" in Path(command[0]).name.lower() and "-version" not in command
        )

    def test_01_zero_start_has_no_extra_delay(self):
        self._set_timing(voice_track_start=0.0, actual_voice_end=7.4)
        self.pipeline().export_current()
        graph = self._ffmpeg_command()[self._ffmpeg_command().index("-filter_complex") + 1]
        self.assertNotIn("adelay", graph)

    def test_02_two_second_start_adds_2000ms_delay(self):
        self.pipeline().export_current()
        command = self._ffmpeg_command()
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("adelay=2000:all=1", graph)

    def test_03_delay_targets_all_mono_or_stereo_channels(self):
        inputs = self.pipeline().collect_inputs()
        command = self.pipeline()._build_ffmpeg_command(
            ffmpeg="ffmpeg", inputs=inputs, output_path=self.paths.exports_dir / "x.mp4"
        )
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn(":all=1", graph)
        self.assertNotIn("2000|2000", graph)

    def test_04_voice_asset_bytes_are_never_modified(self):
        audio = self.paths.voice_version_audio_path(1)
        before = hashlib.sha256(audio.read_bytes()).hexdigest()
        self.pipeline().export_current()
        self.assertEqual(hashlib.sha256(audio.read_bytes()).hexdigest(), before)

    def test_05_saved_start_and_duration_produce_end_nine_point_four(self):
        timing = self.pipeline().collect_inputs().voice_timing
        self.assertEqual(timing["voice_track_start"], 2.0)
        self.assertEqual(timing["actual_audio_duration"], 7.4)
        self.assertEqual(timing["actual_voice_end"], 9.4)

    def test_06_end_inside_video_allows_export(self):
        self.assertEqual(self.pipeline().export_current()["version"], 1)

    def test_07_end_past_probed_video_blocks_export(self):
        runner = TimelineMediaRunner(video_duration=9.0)
        with self.assertRaisesRegex(ExportPipelineError, "exceeds"):
            self.pipeline(runner).export_current()
        self.assertEqual(runner.ffmpeg_calls, 0)

    def test_08_negative_start_blocks_export(self):
        self._set_timing(voice_track_start=-1.0, actual_voice_end=6.4)
        with self.assertRaisesRegex(ExportPipelineError, "不能小于 0"):
            self.pipeline().export_current()

    def test_09_pass_allows_export(self):
        self.assertEqual(
            self.pipeline().collect_inputs().voice_timing["status"], "PASS"
        )
        self.pipeline().export_current()

    def test_10_warning_allows_confirmed_menu_export(self):
        self._set_timing(status="WARNING")
        factory = lambda p, c, l: ExportPipeline(
            p, c, l, runner=self.runner, which=lambda name: name
        )
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["4", "1", "4"]),
            output_fn=lambda _value: None,
            export_pipeline_factory=factory,
        )
        self.assertEqual(self.runner.ffmpeg_calls, 1)

    def test_11_unaccepted_out_of_tolerance_is_blocked(self):
        self._set_timing(status="OUT_OF_TOLERANCE")
        with self.assertRaisesRegex(ExportPipelineError, "明确接受"):
            self.pipeline().export_current()

    def test_12_accepted_out_of_tolerance_is_allowed(self):
        self._set_timing(status="OUT_OF_TOLERANCE")
        self._set_acceptance()
        self.assertEqual(self.pipeline().export_current()["version"], 1)

    def test_13_out_of_bounds_status_is_always_blocked(self):
        self._set_timing(status="OUT_OF_BOUNDS", actual_voice_end=13.0)
        with self.assertRaisesRegex(ExportPipelineError, "exceeds"):
            self.pipeline().export_current()

    def test_14_manual_not_applicable_defaults_to_zero(self):
        self._set_timing(
            status="NOT_APPLICABLE",
            timing_mode="whole_track",
            voice_track_start=0.0,
            actual_voice_end=7.4,
        )
        self.pipeline().export_current()
        graph = self._ffmpeg_command()[self._ffmpeg_command().index("-filter_complex") + 1]
        self.assertNotIn("adelay", graph)

    def test_15_storyboard_srt_file_is_not_shifted(self):
        before = self.paths.subtitle_version_srt_path(1).read_text(encoding="utf-8")
        self.pipeline().export_current()
        copied = (self.paths.export_version_dir(1) / "subtitle.srt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before, copied)

    def test_16_subtitle_filter_uses_existing_absolute_srt(self):
        self.pipeline().export_current()
        command = self._ffmpeg_command()
        subtitle_filter = command[command.index("-vf") + 1]
        self.assertEqual(subtitle_filter.count("subtitles=subtitle.srt"), 1)
        self.assertNotIn("setpts", subtitle_filter)

    def test_17_music_uses_trim_and_pad_without_stream_loop_or_delay(self):
        self.pipeline().export_current()
        command = self._ffmpeg_command()
        graph = command[command.index("-filter_complex") + 1]
        music_branch = next(part for part in graph.split(";") if "[music]" in part and "volume" in part)
        self.assertNotIn("-stream_loop", command)
        self.assertIn("atrim=", music_branch)
        self.assertIn("apad=whole_dur=", music_branch)
        self.assertNotIn("adelay", music_branch)

    def test_18_music_volume_remains_unchanged(self):
        self.pipeline().export_current()
        graph = self._ffmpeg_command()[self._ffmpeg_command().index("-filter_complex") + 1]
        self.assertIn("volume=0.25", graph)

    def test_19_deterministic_music_ducking_uses_no_sidechain(self):
        self.pipeline().export_current()
        graph = self._ffmpeg_command()[self._ffmpeg_command().index("-filter_complex") + 1]
        self.assertNotIn("sidechaincompress", graph)
        self.assertIn(":eval=frame", graph)

    def test_20_start_zero_and_two_have_different_fingerprints(self):
        pipeline = self.pipeline()
        first = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        self._set_timing(voice_track_start=0.0, actual_voice_end=7.4)
        second = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        self.assertNotEqual(first, second)

    def test_21_identical_timing_is_detected_as_existing(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        self.assertIsNotNone(pipeline.find_existing_export())
        with self.assertRaises(ExportAlreadyExistsError):
            pipeline.export_current()

    def test_22_voice_audio_sha_change_changes_fingerprint(self):
        pipeline = self.pipeline()
        first = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        with self.paths.voice_version_audio_path(1).open("ab") as output:
            output.write(b"changed")
        second = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        self.assertNotEqual(first["voice"]["sha256"], second["voice"]["sha256"])

    def test_23_calibration_render_field_changes_fingerprint(self):
        pipeline = self.pipeline()
        first = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        self._set_timing(status="WARNING")
        second = pipeline.build_input_fingerprint(pipeline.collect_inputs())
        self.assertNotEqual(first, second)

    def test_24_timeline_change_creates_v002_without_overwrite(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        first = self.paths.export_version_video_path(1).read_bytes()
        self._set_timing(voice_track_start=0.0, actual_voice_end=7.4)
        second = pipeline.export_current()
        self.assertEqual(second["version"], 2)
        self.assertEqual(self.paths.export_version_video_path(1).read_bytes(), first)

    def test_25_resume_reads_saved_export_voice_timing(self):
        self.pipeline().export_current()
        active = ExportAssetManager(self.paths).active_version()
        self.assertEqual(active["voice"]["voice_track_start"], 2.0)
        self.assertEqual(active["voice"]["calibration_status"], "PASS")

    def test_26_resume_does_not_run_ffmpeg_again(self):
        self.pipeline().export_current()
        before = self.runner.ffmpeg_calls
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
        self.assertEqual(self.runner.ffmpeg_calls, before)
        self.assertIn("Voice Start：2s", "\n".join(output))

    def test_27_legacy_voice_defaults_to_start_zero(self):
        manifest = self._voice_manifest()
        manifest["versions"][0].pop("timing_calibration", None)
        self.paths.save_json(self.paths.voice_manifest_path(), manifest)
        config = json.loads(
            self.paths.voice_version_config_path(1).read_text(encoding="utf-8")
        )
        config.pop("timing_calibration", None)
        self.paths.save_json(self.paths.voice_version_config_path(1), config)
        inputs = self.pipeline().collect_inputs()
        self.assertEqual(inputs.voice_timing["status"], "LEGACY_NO_CALIBRATION")
        self.assertEqual(inputs.voice_timing["voice_track_start"], 0.0)

    def test_28_project_without_voice_still_exports(self):
        self.paths.voice_manifest_path().unlink()
        entry = self.pipeline().export_current()
        self.assertEqual(entry["version"], 1)
        payload = json.loads(
            self.paths.export_version_manifest_path(1).read_text(encoding="utf-8")
        )
        self.assertIsNone(payload["voice"])

    def test_29_old_project_resume_remains_compatible(self):
        self.test_27_legacy_voice_defaults_to_start_zero()
        self.pipeline().export_current()
        loaded = ProjectCheckpoint.load(self.paths)
        PostProductionPipeline(loaded).sync_from_existing_assets()
        self.assertEqual(
            loaded.data["post_production"]["components"]["final_export"]["status"],
            "COMPLETED",
        )

    def test_30_ffmpeg_failure_does_not_commit_version(self):
        runner = TimelineMediaRunner(ffmpeg_failure=True)
        with self.assertRaisesRegex(ExportPipelineError, "FFmpeg"):
            self.pipeline(runner).export_current()
        self.assertFalse(self.paths.export_version_dir(1).exists())
        self.assertIsNone(ExportAssetManager(self.paths).load_manifest()["active_version"])

    def test_31_ffprobe_failure_does_not_commit_success(self):
        runner = TimelineMediaRunner(final_probe_failure=True)
        with self.assertRaisesRegex(ExportPipelineError, "ffprobe"):
            self.pipeline(runner).export_current()
        self.assertFalse(self.paths.export_version_dir(1).exists())

    def test_32_export_never_calls_tts(self):
        calls = self.provider.calls
        self.pipeline().export_current()
        self.assertEqual(self.provider.calls, calls)

    def test_33_export_uses_no_deepseek_or_network(self):
        with patch("socket.socket", side_effect=AssertionError("network used")):
            self.pipeline().export_current()

    def test_34_export_uses_no_minimax_or_external_api(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("API used")):
            self.pipeline().export_current()

    def _assert_source_unchanged(self, filename: str):
        path = Path(__file__).resolve().parent.parent / filename
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.pipeline().collect_inputs()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_35_timeline_scheduler_source_is_unchanged(self):
        self._assert_source_unchanged("timeline_scheduler.py")

    def test_36_subtitle_pipeline_source_is_unchanged(self):
        self._assert_source_unchanged("subtitle_generation.py")

    def test_37_music_pipeline_source_is_unchanged(self):
        self._assert_source_unchanged("music_generation.py")


if __name__ == "__main__":
    unittest.main()
