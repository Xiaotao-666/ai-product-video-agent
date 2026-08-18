from __future__ import annotations

import tempfile
import unittest
import struct
import zlib
import importlib
from pathlib import Path

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ShotStatus
from prompt_generator import ProductVideoRequest
from providers.minimax_h3_provider import MiniMaxH3Provider
from providers.minimax_hailuo_provider import MiniMaxHailuoProvider
from reference_assets import ReferenceAssetManager
from shot_review import create_prompt_version
from storyboard import (
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    VideoPromptPlan,
)
from task_logger import TaskLogger
from video_assembly import approved_shot_inputs
from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_generator import generate_video
from video_history import video_version_history
from video_provider import (
    DownloadResult,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderTask,
    ProviderTaskStatus,
    VideoProvider,
    VideoProviderError,
)
from video_provider_registry import VideoProviderRegistry
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    reference_asset_visual_input,
)


def write_png(path: Path, width: int = 32, height: int = 32) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([64, 96, 128]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class DummyProvider(VideoProvider):
    def __init__(
        self,
        provider: str,
        model: str,
        api_version: str,
        modes: set[str],
        *,
        fail_stage: str | None = None,
        error_code: ProviderErrorCode = ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
    ) -> None:
        self.provider_name = provider
        self.model_name = model
        self.api_version = api_version
        self.capabilities = ProviderCapabilities(frozenset(modes))
        self.generation_mode_by_visual_mode = {
            mode: f"{provider}_{mode}" for mode in modes
        }
        self.fail_stage = fail_stage
        self.error_code = error_code
        self.submit_calls = 0
        self.poll_calls = 0
        self.download_calls = 0

    def _fail(self, stage: str) -> None:
        if self.fail_stage == stage:
            raise VideoProviderError(
                self.error_code,
                f"mock {stage} failure",
                provider=self.provider_name,
                model=self.model_name,
                retryable=self.error_code
                in {
                    ProviderErrorCode.RATE_LIMIT,
                    ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
                },
                raw_error={"mock": stage},
            )

    def submit(self, request, task_logger=None) -> ProviderTask:
        self.submit_calls += 1
        self._fail("submit")
        return ProviderTask(
            provider=self.provider_name,
            model=self.model_name,
            api_version=self.api_version,
            generation_mode=self.generation_mode(request.required_capability),
            provider_task_id=f"{self.provider_name}-task-{self.submit_calls}",
        )

    def poll(self, task, task_logger=None) -> ProviderTask:
        self.poll_calls += 1
        self._fail("poll")
        return task.evolve(
            status=ProviderTaskStatus.COMPLETED,
            raw_status="mock_completed",
            provider_file_id=task.provider_file_id or f"{self.provider_name}-file",
            output_locator=task.output_locator or "mock://video",
        )

    def download(self, task, output_path, request, task_logger=None) -> DownloadResult:
        self.download_calls += 1
        self._fail("download")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(
            f"{self.provider_name}/{self.model_name}/{task.provider_task_id}".encode()
        )
        return DownloadResult(output_path, output_path.stat().st_size)


class VideoProviderArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.logger = TaskLogger(self.paths, "provider-architecture")
        source = Path(self.temp.name) / "reference.png"
        write_png(source)
        asset = ReferenceAssetManager(self.paths, self.logger).import_image(source)
        self.first_frame = first_frame_visual_input(asset)
        self.reference_asset = reference_asset_visual_input(asset)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(
        self,
        visual: dict | None = None,
        *,
        selection: ProviderSelection | None = None,
    ) -> VideoGenerationRequest:
        return VideoGenerationRequest(
            shot_id=1,
            prompt="provider-neutral prompt",
            duration=6,
            resolution="768P",
            visual_input=visual or none_visual_input(),
            project=self.paths,
            provider_selection=selection,
        )

    def builtin_registry(self) -> VideoProviderRegistry:
        config = {
            "default_models": {
                "none": {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"},
                "first_frame": {"provider": "minimax", "model": "MiniMax-Hailuo-2.3"},
                "reference_asset": {"provider": "minimax", "model": "MiniMax-H3"},
            },
            "known_models": {
                "MiniMax-Hailuo-2.3": "minimax",
                "MiniMax-H3": "minimax",
            },
        }
        registry = VideoProviderRegistry(config)
        registry.register(MiniMaxHailuoProvider("mock"))
        registry.register(MiniMaxH3Provider("mock"))
        return registry

    def state(self) -> tuple[ProjectCheckpoint, VideoPromptPlan]:
        request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=6,
            video_style="S",
            video_purpose="U",
        )
        checkpoint = ProjectCheckpoint.create(
            self.paths, "P", request.model_dump()
        )
        checkpoint.ensure_shots([1])
        plan = VideoPromptPlan(
            shots=[ShotVideoPrompt(shot_id=1, video_prompt="prompt-v1")]
        )
        create_prompt_version(
            self.paths,
            checkpoint,
            plan,
            1,
            "prompt-v1",
            "ai_generated",
            self.logger,
            parent_version=None,
        )
        return checkpoint, plan

    def complete_state_generation(
        self,
        checkpoint: ProjectCheckpoint,
        provider: str,
        model: str,
        api_version: str,
    ) -> int:
        checkpoint.prepare_shot_generation(1)
        version = int(checkpoint.shot_checkpoint(1)["pending_video_version"])
        task = ProviderTask(
            provider=provider,
            model=model,
            api_version=api_version,
            generation_mode=f"{provider}_mode",
            provider_task_id=f"{provider}-task-v{version}",
        )
        checkpoint.mark_shot_submitted(1, task)
        checkpoint.mark_shot_task_updated(
            1,
            task.evolve(
                provider_file_id=f"{provider}-file-v{version}",
                status=ProviderTaskStatus.COMPLETED,
            ),
        )
        video = self.paths.shot_version_video_path(1, version)
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(f"video-{provider}-{version}".encode())
        checkpoint.mark_shot_ready_for_review(1)
        return version

    def test_A_default_text_to_video_routes_through_registry(self):
        adapter = self.builtin_registry().resolve(self.request())
        self.assertEqual((adapter.provider_name, adapter.model_name), ("minimax", "MiniMax-Hailuo-2.3"))

    def test_B_default_first_frame_routes_through_registry(self):
        adapter = self.builtin_registry().resolve(self.request(self.first_frame))
        self.assertEqual(adapter.model_name, "MiniMax-Hailuo-2.3")
        self.assertTrue(adapter.supports("first_frame"))

    def test_C_default_reference_asset_routes_through_registry(self):
        adapter = self.builtin_registry().resolve(self.request(self.reference_asset))
        self.assertEqual(adapter.model_name, "MiniMax-H3")
        self.assertTrue(adapter.supports("reference_asset"))

    def test_D_unsupported_capability_is_rejected_before_submit(self):
        request = self.request(
            self.reference_asset,
            selection=ProviderSelection("minimax", "MiniMax-Hailuo-2.3"),
        )
        with self.assertRaises(VideoProviderError) as raised:
            self.builtin_registry().resolve(request)
        self.assertEqual(raised.exception.code, ProviderErrorCode.UNSUPPORTED_CAPABILITY)

    def test_E_hailuo_specific_request_mapping_stays_in_adapter(self):
        payload = MiniMaxHailuoProvider("mock").build_payload(
            self.request(self.first_frame)
        )
        self.assertIn("first_frame_image", payload)
        self.assertNotIn("content", payload)

    def test_F_h3_specific_request_mapping_stays_in_adapter(self):
        payload = MiniMaxH3Provider("mock").build_payload(
            self.request(self.reference_asset)
        )
        self.assertNotIn("first_frame_image", payload)
        self.assertEqual(payload["content"][1]["role"], "reference_image")

    def test_G_business_modules_contain_no_provider_http_contract(self):
        business_modules = (
            "main",
            "shot_manager",
            "shot_review",
            "project_state",
            "shot_storage",
            "video_history",
            "video_assembly",
            "video_generator",
        )
        forbidden = (
            "/v1/video_generation",
            "/v2/video_generation",
            "first_frame_image",
            "MINIMAX_API_KEY",
            "Authorization",
        )
        for name in business_modules:
            module_path = Path(importlib.import_module(name).__file__)
            text = module_path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{name} leaked provider contract {token}")

    def test_H_default_provider_switch_needs_only_registry_config(self):
        config = {
            "default_models": {
                "none": {"provider": "second", "model": "video-b"}
            }
        }
        first = DummyProvider("first", "video-a", "vA", {"none"})
        second = DummyProvider("second", "video-b", "vB", {"none"})
        registry = VideoProviderRegistry(config)
        registry.register(first)
        registry.register(second)
        output = self.paths.shot_version_video_path(1, 1)
        generate_video(
            {},
            "prompt",
            self.paths,
            output_path=output,
            provider_registry=registry,
        )
        self.assertEqual((first.submit_calls, second.submit_calls), (0, 1))

    def test_I_resume_locks_original_provider_even_after_default_changes(self):
        config = {
            "default_models": {
                "none": {"provider": "new", "model": "new-model"}
            }
        }
        old = DummyProvider("old", "old-model", "v-old", {"none"})
        new = DummyProvider("new", "new-model", "v-new", {"none"})
        registry = VideoProviderRegistry(config)
        registry.register(old)
        registry.register(new)
        resume = ProviderTask(
            "old", "old-model", "v-old", "old_none", "old-task"
        )
        generate_video(
            {},
            "prompt",
            self.paths,
            output_path=self.paths.shot_version_video_path(1, 1),
            provider_registry=registry,
            resume_task=resume,
        )
        self.assertEqual((old.submit_calls, old.poll_calls), (0, 1))
        self.assertEqual((new.submit_calls, new.poll_calls), (0, 0))

    def test_J_one_shot_history_can_hold_multiple_providers(self):
        checkpoint, _plan = self.state()
        self.complete_state_generation(checkpoint, "alpha", "model-a", "v1")
        self.complete_state_generation(checkpoint, "beta", "model-b", "v2")
        history = video_version_history(self.paths, checkpoint, 1)
        self.assertEqual([item.provider for item in history], ["alpha", "beta"])
        self.assertEqual([item.provider_model for item in history], ["model-a", "model-b"])

    def test_K_candidate_provider_isolated_from_approved_provider(self):
        checkpoint, _plan = self.state()
        self.complete_state_generation(checkpoint, "alpha", "model-a", "v1")
        checkpoint.approve_shot(1)
        checkpoint.begin_candidate_editing(1, None)
        checkpoint.candidate_checkpoint(1)["prompt_version"] = 1
        checkpoint.save()
        checkpoint.prepare_candidate_generation(1)
        checkpoint.mark_candidate_submitted(
            1,
            ProviderTask("beta", "model-b", "v2", "beta_mode", "candidate-task"),
        )
        entry = checkpoint.shot_checkpoint(1)
        approved_generation = next(
            item for item in entry["generation_versions"] if item["video_version"] == 1
        )
        self.assertEqual(approved_generation["provider"], "alpha")
        self.assertEqual(checkpoint.candidate_checkpoint(1)["provider"], "beta")

    def test_L_provider_errors_use_stable_generic_codes(self):
        failing = DummyProvider(
            "rate-limited",
            "video-rate",
            "v1",
            {"none"},
            fail_stage="submit",
            error_code=ProviderErrorCode.RATE_LIMIT,
        )
        registry = VideoProviderRegistry(
            {"default_models": {"none": {"provider": "rate-limited", "model": "video-rate"}}}
        )
        registry.register(failing)
        with self.assertRaises(VideoProviderError) as raised:
            generate_video(
                {},
                "prompt",
                self.paths,
                output_path=self.paths.shot_version_video_path(1, 1),
                provider_registry=registry,
            )
        self.assertEqual(raised.exception.code, ProviderErrorCode.RATE_LIMIT)

    def test_M_download_failure_keeps_resume_checkpoint(self):
        checkpoint, _plan = self.state()
        checkpoint.prepare_shot_generation(1)
        failing = DummyProvider(
            "download-test",
            "video-download",
            "v1",
            {"none"},
            fail_stage="download",
            error_code=ProviderErrorCode.DOWNLOAD_FAILED,
        )
        registry = VideoProviderRegistry(
            {"default_models": {"none": {"provider": "download-test", "model": "video-download"}}}
        )
        registry.register(failing)
        with self.assertRaises(VideoProviderError):
            generate_video(
                {},
                "prompt",
                self.paths,
                output_path=self.paths.shot_version_video_path(1, 1),
                provider_registry=registry,
                on_submitted=lambda task: checkpoint.mark_shot_submitted(1, task),
                on_task_updated=lambda task: checkpoint.mark_shot_task_updated(1, task),
            )
        resumed = checkpoint.generation_provider_task(1, 1)
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.provider_task_id, "download-test-task-1")
        self.assertEqual(resumed.provider_file_id, "download-test-file")
        self.assertEqual(checkpoint.shot_status(1), ShotStatus.GENERATING)

    def test_N_assembly_remains_provider_agnostic(self):
        checkpoint, _plan = self.state()
        self.complete_state_generation(checkpoint, "third-party", "model-z", "v9")
        checkpoint.approve_shot(1)
        board = Storyboard(
            total_duration=6,
            shots=[StoryboardShot(shot_id=1, duration=6, purpose="p", visual="v", camera="c")],
        )
        selected = approved_shot_inputs(self.paths, checkpoint, board)
        self.assertEqual(selected[0]["path"], self.paths.shot_version_video_path(1, 1))

    def test_O_legacy_bundle_provider_can_be_inferred_from_known_model(self):
        config = {
            "default_models": {"reference_asset": {"provider": "new", "model": "new-model"}},
            "known_models": {"MiniMax-H3": "minimax"},
        }
        registry = VideoProviderRegistry(config)
        historical = DummyProvider("minimax", "MiniMax-H3", "v2", {"reference_asset"})
        registry.register(historical)
        registry.register(DummyProvider("new", "new-model", "v9", {"reference_asset"}))
        task = ProviderTask(None, "MiniMax-H3", "v2", "reference_generation", "legacy-task")
        self.assertIs(registry.resolve(self.request(self.reference_asset), task), historical)

    def test_P_new_provider_needs_adapter_registration_not_business_changes(self):
        future = DummyProvider("future", "future-video-1", "v42", {"none"})
        registry = VideoProviderRegistry(
            {"default_models": {"none": {"provider": "future", "model": "future-video-1"}}}
        )
        registry.register(future)
        output = generate_video(
            {},
            "prompt",
            self.paths,
            output_path=self.paths.shot_version_video_path(1, 1),
            provider_registry=registry,
        )
        self.assertTrue(output.is_file())
        self.assertEqual(future.submit_calls, 1)


if __name__ == "__main__":
    unittest.main()
