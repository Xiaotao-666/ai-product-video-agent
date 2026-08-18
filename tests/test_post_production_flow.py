from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path

from export_pipeline import ExportPipeline
from post_production import ProjectCompletionStatus
from post_production_menu import post_production_menu, project_resume_menu
from project_manager import create_project_paths
from project_state import AssemblyStatus, ProjectCheckpoint, ProjectStage, StageStatus
from task_logger import TaskLogger
from voice_assets import VoiceAssetManager
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
)
from voice_provider_registry import VoiceProviderRegistry


def silent_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\x00\x00" * 400)
    return buffer.getvalue()


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

    def generate_voice(self, request: VoiceGenerationRequest) -> VoiceGenerationResult:
        self.calls += 1
        return VoiceGenerationResult(
            audio_bytes=silent_wav(),
            duration_seconds=0.05,
            provider_task_id=f"mock-{self.calls}",
        )


class Inputs:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)

    def __call__(self, _prompt: str = "") -> str:
        return next(self.values)


class LocalExportRunner:
    def __call__(self, command: list[str], **_kwargs):
        executable = Path(command[0]).name.lower()
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "mock tool\n", "")
        if "ffprobe" in executable:
            payload = json.dumps(
                {
                    "format": {"duration": "6.0"},
                    "streams": [
                        {"index": 0, "codec_type": "video", "codec_name": "h264"}
                    ],
                }
            )
            return subprocess.CompletedProcess(command, 0, payload, "")
        Path(command[-1]).write_bytes(b"mock-assembled-video")
        return subprocess.CompletedProcess(command, 0, "", "")


class PostProductionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Post Test",
            {
                "product_name": "Product",
                "product_description": "Description",
                "user_notes": "",
            },
        )
        self.final_video = self.paths.final_video_path()
        self.final_video.write_bytes(b"mock-assembled-video")
        self.checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "assembled_at": "2026-08-16T20:00:00+08:00",
                "total_duration": 6.0,
            }
        )
        self.checkpoint.save()
        self.logger = TaskLogger(self.paths, "post-test")
        self.provider = MockVoiceProvider()
        self.registry = VoiceProviderRegistry({"default_provider": "mock_voice"})
        self.registry.register(self.provider)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_1_old_completed_project_resumes_into_postproduction(self):
        stored = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        stored.pop("voice_config", None)
        stored.pop("post_production", None)
        stored.pop("completion_status", None)
        stored["stages"][ProjectStage.COMPLETED.value]["status"] = (
            StageStatus.COMPLETED.value
        )
        self.paths.save_json(self.paths.project_state_path(), stored)

        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(
            loaded.data["completion_status"],
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value,
        )
        project_resume_menu(
            self.paths,
            loaded,
            self.logger,
            regenerate_assembly=lambda: None,
            open_shot_management=lambda: None,
            voice_registry=self.registry,
            input_fn=Inputs(["2", "5", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(
            loaded.data["completion_status"],
            ProjectCompletionStatus.POST_PRODUCTION.value,
        )

    def test_2_existing_final_video_never_calls_video_generation(self):
        calls = {"assembly": 0, "shot": 0}

        def assembly() -> None:
            calls["assembly"] += 1

        def shot() -> None:
            calls["shot"] += 1

        project_resume_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            regenerate_assembly=assembly,
            open_shot_management=shot,
            input_fn=Inputs(["5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(calls, {"assembly": 0, "shot": 0})
        self.assertEqual(self.provider.calls, 0)

    def test_3_voice_can_be_generated_through_existing_pipeline(self):
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(
                ["1", "1", "欢迎了解产品。", "END", "", "1", "5"]
            ),
            output_fn=lambda _value: None,
        )
        active = VoiceAssetManager(self.paths).active_version()
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(active["version"], 1)
        self.assertTrue(self.checkpoint.data["voice_config"]["enabled"])
        self.assertEqual(
            self.checkpoint.data["post_production"]["components"]["voice"][
                "status"
            ],
            "COMPLETED",
        )

    def test_4_voice_resume_does_not_repeat_tts(self):
        VoiceAssetManager(self.paths).generate_and_save(
            VoiceGenerationRequest("已有配音", "xiaoyun", "zh-CN"),
            self.provider,
        )
        self.assertEqual(self.provider.calls, 1)
        loaded = ProjectCheckpoint.load(self.paths)
        post_production_menu(
            self.paths,
            loaded,
            self.logger,
            voice_registry=self.registry,
            input_fn=Inputs(["1", "1", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.provider.calls, 1)

    def test_5_shot_management_entry_is_unchanged(self):
        calls = {"shot": 0}

        def shot() -> None:
            calls["shot"] += 1

        project_resume_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            regenerate_assembly=lambda: None,
            open_shot_management=shot,
            input_fn=Inputs(["4", "5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(calls["shot"], 1)

    def test_6_postproduction_state_does_not_mutate_assembly_or_shots(self):
        shots_before = deepcopy(self.checkpoint.data["video_generation"])
        assembly_before = deepcopy(self.checkpoint.data["assembly"])
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["5"]),
            output_fn=lambda _value: None,
        )
        self.assertEqual(self.checkpoint.data["video_generation"], shots_before)
        self.assertEqual(self.checkpoint.data["assembly"], assembly_before)

    def test_7_final_export_is_versioned_and_requires_no_provider(self):
        runner = LocalExportRunner()
        factory = lambda paths, checkpoint, logger: ExportPipeline(
            paths,
            checkpoint,
            logger,
            runner=runner,
            which=lambda name: name,
        )
        post_production_menu(
            self.paths,
            self.checkpoint,
            self.logger,
            input_fn=Inputs(["4", "1", "3", "4", "2", "3", "5"]),
            output_fn=lambda _value: None,
            export_pipeline_factory=factory,
        )
        first = self.paths.export_version_video_path(1)
        second = self.paths.export_version_video_path(2)
        self.assertEqual(first.read_bytes(), b"mock-assembled-video")
        self.assertEqual(second.read_bytes(), b"mock-assembled-video")
        self.assertEqual(self.provider.calls, 0)
        self.assertEqual(
            self.checkpoint.data["completion_status"],
            ProjectCompletionStatus.FINAL_COMPLETED.value,
        )
        manifest = json.loads(
            self.paths.export_manifest_path().read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["active_version"], 2)
        self.assertEqual(len(manifest["versions"]), 2)


if __name__ == "__main__":
    unittest.main()
