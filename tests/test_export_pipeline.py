from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from export_assets import ExportAssetManager
from export_pipeline import (
    ExportAlreadyExistsError,
    ExportPipeline,
    ExportPipelineError,
)
from music_assets import MusicAssetManager
from music_generation import add_local_music
from music_provider_registry import build_music_provider_registry
from post_production import PostProductionPipeline, ProjectCompletionStatus
from post_production_menu import post_production_menu, project_resume_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint
from subtitle_assets import SubtitleAssetManager
from subtitle_generation import generate_subtitle_from_active_voice
from subtitle_provider_registry import build_subtitle_provider_registry
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
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
    provider_name = "local_export_voice"
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
            provider_task_id="local-export-voice",
        )


class FakeMediaRunner:
    """A local command harness; it never starts a process or network request."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.ffmpeg_calls = 0

    def __call__(self, command: list[str], **_kwargs):
        self.commands.append(list(command))
        executable = Path(command[0]).name.lower()
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, f"{executable} mock 1.0\n", "")
        if "ffprobe" in executable:
            target = Path(command[-1])
            if target.suffix.lower() in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}:
                streams = [{"index": 0, "codec_type": "audio", "codec_name": "pcm_s16le"}]
            else:
                streams = [
                    {"index": 0, "codec_type": "video", "codec_name": "h264"},
                    {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                ]
            payload = json.dumps({"format": {"duration": "2.000"}, "streams": streams})
            return subprocess.CompletedProcess(command, 0, payload, "")
        if "ffmpeg" in executable:
            self.ffmpeg_calls += 1
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(f"mock-final-export-{self.ffmpeg_calls}".encode())
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unknown command")


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class ExportPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = create_project_paths(root / "project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Export Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        self.paths.final_video_path().write_bytes(b"mock-assembled-video")
        self.checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 3,
                "assembled_at": "2026-08-17T12:00:00+08:00",
                "total_duration": 2.0,
            }
        )
        self.checkpoint.save()
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest(
                script="产品清新自然。",
                voice="local",
                language="zh-CN",
            ),
            LocalVoiceProvider(),
        )
        generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )
        self.music_source = root / "music.wav"
        self.music_source.write_bytes(silent_wav())
        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_source,
            music_volume=0.25,
        )
        self.logger = TaskLogger(self.paths, "export-test")
        self.runner = FakeMediaRunner()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def pipeline(self) -> ExportPipeline:
        return ExportPipeline(
            self.paths,
            self.checkpoint,
            self.logger,
            runner=self.runner,
            which=lambda name: name,
        )

    def test_A_video_voice_music_are_mixed_and_saved(self):
        entry = self.pipeline().export_current()
        self.assertEqual(entry["version"], 1)
        self.assertTrue(self.paths.export_version_video_path(1).is_file())
        ffmpeg = next(
            command
            for command in self.runner.commands
            if "ffmpeg" in Path(command[0]).name.lower() and "-version" not in command
        )
        filters = ffmpeg[ffmpeg.index("-filter_complex") + 1]
        self.assertIn("volume=1.0", filters)
        self.assertIn("volume=0.25", filters)
        self.assertIn("amix=inputs=2", filters)
        self.assertIn("-shortest", ffmpeg)

    def test_B_subtitle_is_burned_bottom_center_white_with_black_outline(self):
        self.pipeline().export_current()
        ffmpeg = next(
            command
            for command in self.runner.commands
            if "ffmpeg" in Path(command[0]).name.lower() and "-version" not in command
        )
        subtitle_filter = ffmpeg[ffmpeg.index("-vf") + 1]
        self.assertIn("subtitles=subtitle.srt", subtitle_filter)
        self.assertIn("Alignment=2", subtitle_filter)
        self.assertIn("PrimaryColour=&H00FFFFFF", subtitle_filter)
        self.assertIn("OutlineColour=&H00000000", subtitle_filter)
        self.assertIn("Outline=2", subtitle_filter)

    def test_C_manifest_records_exact_selected_versions_and_settings(self):
        self.pipeline().export_current()
        payload = json.loads(
            self.paths.export_version_manifest_path(1).read_text(encoding="utf-8")
        )
        self.assertEqual(payload["video_version"], 3)
        self.assertEqual(payload["voice_version"], 1)
        self.assertEqual(payload["subtitle_version"], 1)
        self.assertEqual(payload["music_version"], 1)
        self.assertEqual(payload["settings"]["voice_volume"], 1.0)
        self.assertEqual(payload["settings"]["music_volume"], 0.25)
        self.assertTrue(payload["audio_muxed"])
        self.assertTrue(payload["subtitle_burned"])
        fingerprint = payload["input_fingerprint"]
        self.assertEqual(len(fingerprint["video"]["sha256"]), 64)
        self.assertEqual(len(fingerprint["voice"]["sha256"]), 64)
        self.assertEqual(len(fingerprint["subtitle"]["sha256"]), 64)
        self.assertEqual(len(fingerprint["music"]["sha256"]), 64)
        self.assertEqual(len(payload["input_fingerprint_sha256"]), 64)

    def test_D_reexport_creates_v002_without_overwriting_v001(self):
        self.pipeline().export_current()
        first_payload = self.paths.export_version_video_path(1).read_bytes()
        self.pipeline().export_current(force=True)
        self.assertEqual(
            self.paths.export_version_video_path(1).read_bytes(), first_payload
        )
        self.assertNotEqual(
            self.paths.export_version_video_path(2).read_bytes(), first_payload
        )
        manifest = ExportAssetManager(self.paths).load_manifest()
        self.assertEqual(manifest["active_version"], 2)
        self.assertEqual(len(manifest["versions"]), 2)

    def test_E_resume_reads_active_export_without_reencoding_or_regeneration(self):
        self.pipeline().export_current()
        calls_before = len(self.runner.commands)
        loaded = ProjectCheckpoint.load(self.paths)
        PostProductionPipeline(loaded).sync_from_existing_assets()
        self.assertEqual(len(self.runner.commands), calls_before)
        component = loaded.data["post_production"]["components"]["final_export"]
        self.assertEqual(component["active_version"], 1)
        self.assertEqual(component["status"], "COMPLETED")
        self.assertEqual(
            loaded.data["completion_status"],
            ProjectCompletionStatus.FINAL_COMPLETED.value,
        )

    def test_F_missing_tools_fails_before_creating_export_version(self):
        pipeline = ExportPipeline(
            self.paths,
            self.checkpoint,
            self.logger,
            runner=self.runner,
            which=lambda _name: None,
        )
        with self.assertRaisesRegex(ExportPipelineError, "ffmpeg, ffprobe"):
            pipeline.export_current()
        self.assertFalse(self.paths.export_version_dir(1).exists())
        self.assertEqual(self.runner.ffmpeg_calls, 0)

    def test_G_export_never_calls_video_voice_subtitle_music_providers(self):
        before = {
            "assembly": self.paths.final_video_path().read_bytes(),
            "voice": VoiceAssetManager(self.paths).load_manifest(),
            "subtitle": SubtitleAssetManager(self.paths).load_manifest(),
            "music": MusicAssetManager(self.paths).load_manifest(),
        }
        self.pipeline().export_current()
        self.assertEqual(self.paths.final_video_path().read_bytes(), before["assembly"])
        self.assertEqual(VoiceAssetManager(self.paths).load_manifest(), before["voice"])
        self.assertEqual(
            SubtitleAssetManager(self.paths).load_manifest(), before["subtitle"]
        )
        self.assertEqual(MusicAssetManager(self.paths).load_manifest(), before["music"])

    def test_H_identical_inputs_find_existing_and_default_does_not_create_v002(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        ffmpeg_before = self.runner.ffmpeg_calls
        existing = pipeline.find_existing_export()
        self.assertIsNotNone(existing)
        self.assertEqual(existing["entry"]["version"], 1)
        with self.assertRaises(ExportAlreadyExistsError):
            pipeline.export_current()
        self.assertEqual(self.runner.ffmpeg_calls, ffmpeg_before)
        self.assertFalse(self.paths.export_version_dir(2).exists())

    def test_I_force_identical_export_creates_next_version(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        second = pipeline.export_current(force=True)
        self.assertEqual(second["version"], 2)
        self.assertTrue(self.paths.export_version_video_path(1).is_file())
        self.assertTrue(self.paths.export_version_video_path(2).is_file())

    def test_J_changed_voice_subtitle_or_music_version_allows_normal_export(self):
        pipeline = self.pipeline()
        pipeline.export_current()

        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest("新版配音。", "local", "zh-CN"),
            LocalVoiceProvider(),
        )
        self.assertEqual(pipeline.export_current()["version"], 2)

        generate_subtitle_from_active_voice(
            SubtitleAssetManager(self.paths),
            build_subtitle_provider_registry(),
        )
        self.assertEqual(pipeline.export_current()["version"], 3)

        add_local_music(
            MusicAssetManager(self.paths),
            build_music_provider_registry(),
            self.music_source,
            music_volume=0.25,
        )
        self.assertEqual(pipeline.export_current()["version"], 4)

    def test_K_music_volume_change_invalidates_fingerprint(self):
        pipeline = self.pipeline()
        pipeline.export_current()
        music_manager = MusicAssetManager(self.paths)
        manifest = music_manager.load_manifest()
        manifest["versions"][0]["music_volume"] = 0.5
        self.paths.save_json(self.paths.music_manifest_path(), manifest)
        second = pipeline.export_current()
        self.assertEqual(second["version"], 2)
        payload = json.loads(
            self.paths.export_version_manifest_path(2).read_text(encoding="utf-8")
        )
        self.assertEqual(payload["settings"]["music_volume"], 0.5)

    def test_L_resume_displays_active_export_without_ffmpeg(self):
        self.pipeline().export_current()
        calls_before = self.runner.ffmpeg_calls
        loaded = ProjectCheckpoint.load(self.paths)
        output: list[str] = []
        project_resume_menu(
            self.paths,
            loaded,
            self.logger,
            regenerate_assembly=lambda: None,
            open_shot_management=lambda: None,
            input_fn=Inputs(["5"]),
            output_fn=output.append,
        )
        rendered = "\n".join(output)
        self.assertIn("最终导出", rendered)
        self.assertIn("当前 v001", rendered)
        self.assertEqual(self.runner.ffmpeg_calls, calls_before)

    def test_M_postproduction_state_remains_isolated_from_video_and_shots(self):
        assembly_before = json.loads(json.dumps(self.checkpoint.data["assembly"]))
        shots_before = json.loads(json.dumps(self.checkpoint.data["video_generation"]))
        self.pipeline().export_current()
        self.assertEqual(self.checkpoint.data["assembly"], assembly_before)
        self.assertEqual(self.checkpoint.data["video_generation"], shots_before)

    def test_N_duplicate_menu_return_does_not_create_version(self):
        self.pipeline().export_current()
        ffmpeg_before = self.runner.ffmpeg_calls
        output: list[str] = []
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["4", "3", "5"]),
            output_fn=output.append,
            export_pipeline_factory=lambda paths, checkpoint, logger: ExportPipeline(
                paths,
                checkpoint,
                logger,
                runner=self.runner,
                which=lambda name: name,
            ),
        )
        self.assertIn("Export Already Exists", "\n".join(output))
        self.assertEqual(self.runner.ffmpeg_calls, ffmpeg_before)
        self.assertFalse(self.paths.export_version_dir(2).exists())


if __name__ == "__main__":
    unittest.main()
