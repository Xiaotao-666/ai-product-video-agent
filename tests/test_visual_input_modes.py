from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager
from shot_review import create_prompt_version
from shot_storage import read_bundle_json
from storyboard import ShotVideoPrompt, Storyboard, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_assembly import approved_shot_inputs
from providers.minimax_h3_provider import MiniMaxH3Provider
from providers.minimax_hailuo_provider import MiniMaxHailuoProvider
from video_generation_request import VideoGenerationRequest
from video_generator import generate_video
from video_provider import DownloadResult, ProviderTask, ProviderTaskStatus
from video_provider_registry import create_default_registry
from video_history import video_version_history
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    normalize_visual_input,
    reference_asset_visual_input,
)


def write_png(path: Path, color: int = 64) -> None:
    width = height = 32

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([color, color, color]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class VisualInputModeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.logger = TaskLogger(self.paths, "visual-mode-routing")
        self.assets = ReferenceAssetManager(self.paths, self.logger)
        source = Path(self.temp.name) / "reference.png"
        write_png(source)
        asset = self.assets.import_image(source)
        self.first_frame = first_frame_visual_input(asset)
        self.reference_asset = reference_asset_visual_input(asset)
        request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=12,
            video_style="S",
            video_purpose="U",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "P", request.model_dump()
        )
        self.checkpoint.ensure_shots([1, 2])
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(shot_id=1, video_prompt="prompt-1"),
                ShotVideoPrompt(shot_id=2, video_prompt="prompt-2"),
            ]
        )
        for shot_id in (1, 2):
            create_prompt_version(
                self.paths,
                self.checkpoint,
                self.plan,
                shot_id,
                f"prompt-{shot_id}",
                "ai_generated",
                self.logger,
                parent_version=None,
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _request(self, visual: dict) -> VideoGenerationRequest:
        return VideoGenerationRequest(
            shot_id=1,
            prompt="prompt",
            duration=6,
            resolution="768P",
            visual_input=visual,
            project=self.paths,
        )

    def _route(self, visual: dict) -> dict[str, str]:
        registry = create_default_registry({"minimax": "mock"})
        return registry.provider_metadata(self._request(visual))

    def _payload(self, visual: dict) -> dict:
        request = self._request(visual)
        registry = create_default_registry({"minimax": "mock"})
        adapter = registry.resolve(request)
        return adapter.build_payload(request)

    def _complete(self, shot_id: int, visual: dict) -> int:
        self.checkpoint.set_shot_visual_input(shot_id, visual)
        self.checkpoint.prepare_shot_generation(shot_id)
        version = int(
            self.checkpoint.shot_checkpoint(shot_id)["pending_video_version"]
        )
        route = self._route(visual)
        self.checkpoint.mark_shot_submitted(
            shot_id,
            ProviderTask(
                provider=route["provider"],
                model=route["provider_model"],
                api_version=route["provider_api_version"],
                generation_mode=route["generation_mode"],
                provider_task_id=f"task-{shot_id}-{version}",
            ),
        )
        if route["provider_api_version"] == "v1":
            self.checkpoint.mark_shot_file_ready(shot_id, f"file-{shot_id}-{version}")
        self.paths.shot_version_video_path(shot_id, version).write_bytes(
            f"video-{shot_id}-{version}".encode()
        )
        self.checkpoint.mark_shot_ready_for_review(shot_id)
        return version

    def test_A_none_routes_to_current_t2v(self):
        route = self._route(none_visual_input())
        payload = self._payload(none_visual_input())
        self.assertEqual(route["generation_mode"], "text_to_video")
        self.assertEqual(route["provider_model"], "MiniMax-Hailuo-2.3")
        self.assertNotIn("first_frame_image", payload)
        self.assertNotIn("content", payload)

    def test_B_first_frame_routes_to_hailuo_v1(self):
        route = self._route(self.first_frame)
        payload = self._payload(self.first_frame)
        self.assertEqual(route, {
            "provider": "minimax",
            "generation_mode": "first_frame",
            "provider_model": "MiniMax-Hailuo-2.3",
            "provider_api_version": "v1",
        })
        self.assertIn("first_frame_image", payload)

    def test_C_reference_asset_routes_to_h3_reference_role(self):
        route = self._route(self.reference_asset)
        payload = self._payload(self.reference_asset)
        self.assertEqual(route["provider_model"], "MiniMax-H3")
        self.assertEqual(route["provider_api_version"], "v2")
        image = payload["content"][1]
        self.assertEqual(image["type"], "image_url")
        self.assertEqual(image["role"], "reference_image")

    def test_D_reference_asset_never_sends_first_frame_image(self):
        payload = self._payload(self.reference_asset)
        self.assertNotIn("first_frame_image", payload)

    def test_E_first_frame_is_not_subject_reference(self):
        payload = self._payload(self.first_frame)
        self.assertNotIn("content", payload)
        self.assertEqual(self.first_frame["assets"][0]["role"], "first_frame")

    def test_F_legacy_reference_image_maps_to_first_frame(self):
        legacy = {
            **self.first_frame,
            "mode": "reference_image",
            "assets": [{**self.first_frame["assets"][0], "role": "start_frame"}],
        }
        normalized = normalize_visual_input(legacy)
        self.assertEqual(normalized["mode"], "first_frame")
        self.assertEqual(normalized["assets"][0]["role"], "first_frame")

    def test_G_history_distinguishes_same_asset_modes_and_models(self):
        self._complete(1, self.reference_asset)
        self._complete(1, self.first_frame)
        history = video_version_history(self.paths, self.checkpoint, 1)
        self.assertEqual(
            [(item.visual_input_mode, item.provider_model) for item in history],
            [
                ("reference_asset", "MiniMax-H3"),
                ("first_frame", "MiniMax-Hailuo-2.3"),
            ],
        )
        self.assertEqual(
            history[0].reference_asset_ids, history[1].reference_asset_ids
        )
        bundle_v1 = read_bundle_json(self.paths, 1, 1, "generation.json")
        bundle_v2 = read_bundle_json(self.paths, 1, 2, "generation.json")
        self.assertEqual(bundle_v1["generation_mode"], "reference_generation")
        self.assertEqual(bundle_v1["provider_api_version"], "v2")
        self.assertEqual(bundle_v2["generation_mode"], "first_frame")
        self.assertEqual(bundle_v2["provider_api_version"], "v1")

    def test_H_reference_asset_resume_reuses_h3_task(self):
        output = self.paths.shot_version_video_path(1, 1)
        output.parent.mkdir(parents=True, exist_ok=True)

        registry = create_default_registry({"minimax": "mock"})
        adapter = registry.adapter("minimax", "MiniMax-H3")

        def fake_download(task, output_path, request, task_logger=None):
            output_path.write_bytes(b"resumed-h3")
            return DownloadResult(output_path, output_path.stat().st_size)

        resume_task = ProviderTask(
            provider="minimax",
            model="MiniMax-H3",
            api_version="v2",
            generation_mode="reference_generation",
            provider_task_id="h3-task-existing",
        )
        with (
            patch.object(adapter, "submit", side_effect=AssertionError("must not submit")) as submit,
            patch.object(
                adapter,
                "poll",
                return_value=resume_task.evolve(
                    output_locator="https://download.invalid/video.mp4",
                    status=ProviderTaskStatus.COMPLETED,
                ),
            ) as poll,
            patch.object(adapter, "download", side_effect=fake_download),
        ):
            generate_video(
                {"minimax": "mock-key"},
                "prompt",
                self.paths,
                output_path=output,
                visual_input=self.reference_asset,
                provider_registry=registry,
                resume_task=resume_task,
            )
        submit.assert_not_called()
        poll.assert_called_once()
        self.assertEqual(poll.call_args.args[0].provider_task_id, "h3-task-existing")

    def test_I_candidate_mode_change_does_not_mutate_approved(self):
        self._complete(1, self.reference_asset)
        self.checkpoint.approve_shot(1)
        self.checkpoint.begin_candidate_editing(1, None)
        self.checkpoint.set_candidate_visual_input(1, self.first_frame)
        self.assertEqual(
            self.checkpoint.approved_visual_input(1)["mode"], "reference_asset"
        )
        self.assertEqual(
            self.checkpoint.candidate_checkpoint(1)["visual_input"]["mode"],
            "first_frame",
        )

    def test_J_assembly_is_unchanged_and_uses_approved_bundles(self):
        board = Storyboard(
            total_duration=12,
            shots=[
                StoryboardShot(shot_id=1, duration=6, purpose="p", visual="v", camera="c"),
                StoryboardShot(shot_id=2, duration=6, purpose="p", visual="v", camera="c"),
            ],
        )
        self._complete(1, self.reference_asset)
        self._complete(2, self.first_frame)
        self.checkpoint.approve_shot(1)
        self.checkpoint.approve_shot(2)
        selected = approved_shot_inputs(self.paths, self.checkpoint, board)
        self.assertEqual([item["shot_id"] for item in selected], [1, 2])
        self.assertTrue(all(item["path"].name == "video.mp4" for item in selected))


if __name__ == "__main__":
    unittest.main()
