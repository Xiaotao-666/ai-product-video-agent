from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ShotStatus
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager
from shot_review import create_prompt_version
from shot_storage import read_bundle_json
from storyboard import ShotVideoPrompt, Storyboard, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_assembly import approved_shot_inputs
from providers.minimax_hailuo_provider import MiniMaxHailuoProvider
from video_generation_request import VideoGenerationRequest
from video_generator import generate_video
from video_provider import DownloadResult, ProviderTask, ProviderTaskStatus
from video_provider_registry import create_default_registry
from video_history import video_version_history
from visual_input import (
    VisualInputNotImplementedError,
    none_visual_input,
    reference_visual_input,
)


def write_png(path: Path, width: int = 320, height: int = 320, color: int = 64) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    row = b"\x00" + bytes([color, color, color]) * width
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(payload)


class VisualInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.paths = create_project_paths(self.base / "project")
        self.logger = TaskLogger(self.paths, "visual-input-test")
        self.assets = ReferenceAssetManager(self.paths, self.logger)
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
                ShotVideoPrompt(shot_id=1, video_prompt="prompt-1-v1"),
                ShotVideoPrompt(shot_id=2, video_prompt="prompt-2-v1"),
            ]
        )
        for shot_id in (1, 2):
            create_prompt_version(
                self.paths,
                self.checkpoint,
                self.plan,
                shot_id,
                f"prompt-{shot_id}-v1",
                "ai_generated",
                self.logger,
                parent_version=None,
            )
        self.board = Storyboard(
            total_duration=12,
            shots=[
                StoryboardShot(
                    shot_id=shot_id,
                    duration=6,
                    purpose="p",
                    visual="v",
                    camera="c",
                )
                for shot_id in (1, 2)
            ],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def import_ref(self, name: str, color: int) -> tuple[dict, Path]:
        source = self.base / name
        write_png(source, color=color)
        asset = self.assets.import_image(source)
        return reference_visual_input(asset), source

    def hailuo_payload(self, visual: dict) -> dict:
        request = VideoGenerationRequest(
            shot_id=1,
            prompt="prompt",
            duration=6,
            resolution="768P",
            visual_input=visual,
            project=self.paths,
        )
        return MiniMaxHailuoProvider("mock").build_payload(request)

    def generate(self, shot_id: int, visual: dict, *, task: str | None = None) -> int:
        self.checkpoint.set_shot_visual_input(shot_id, visual)
        self.checkpoint.prepare_shot_generation(shot_id)
        version = int(self.checkpoint.shot_checkpoint(shot_id)["pending_video_version"])
        self.checkpoint.mark_shot_submitted(shot_id, task or f"task-{shot_id}-{version}")
        self.checkpoint.mark_shot_file_ready(shot_id, f"file-{shot_id}-{version}")
        self.paths.shot_version_video_path(shot_id, version).write_bytes(
            f"video-{shot_id}-{version}".encode()
        )
        self.checkpoint.mark_shot_ready_for_review(shot_id)
        return version

    def test_A_none_visual_uses_t2v_payload(self):
        payload = self.hailuo_payload(none_visual_input())
        self.assertNotIn("first_frame_image", payload)

    def test_B_reference_image_uses_i2v_and_bundle_snapshot(self):
        visual, _ = self.import_ref("ref1.png", 40)
        payload = self.hailuo_payload(visual)
        self.assertTrue(payload["first_frame_image"].startswith("data:image/png;base64,"))
        self.generate(1, visual)
        self.assertEqual(
            read_bundle_json(self.paths, 1, 1, "generation.json")["visual_input"],
            visual,
        )

    def test_C_each_shot_keeps_independent_reference(self):
        ref1, _ = self.import_ref("one.png", 10)
        ref2, _ = self.import_ref("two.png", 20)
        self.generate(1, ref1)
        self.generate(2, ref2)
        self.assertNotEqual(
            read_bundle_json(self.paths, 1, 1, "generation.json")["visual_input"],
            read_bundle_json(self.paths, 2, 1, "generation.json")["visual_input"],
        )

    def test_D_same_prompt_reference_can_create_multiple_video_versions(self):
        visual, _ = self.import_ref("same.png", 30)
        self.generate(1, visual)
        self.generate(1, visual)
        for version in (1, 2):
            generation = read_bundle_json(self.paths, 1, version, "generation.json")
            self.assertEqual(generation["prompt_version"], 1)
            self.assertEqual(generation["visual_input"], visual)

    def test_E_new_prompt_and_reference_do_not_change_old_snapshots(self):
        ref1, _ = self.import_ref("old.png", 50)
        ref2, _ = self.import_ref("new.png", 60)
        self.generate(1, ref1)
        create_prompt_version(
            self.paths,
            self.checkpoint,
            self.plan,
            1,
            "prompt-1-v2",
            "manual_edit",
            self.logger,
            parent_version=1,
            original_prompt="prompt-1-v1",
        )
        self.generate(1, ref2)
        self.assertEqual(read_bundle_json(self.paths, 1, 1, "generation.json")["visual_input"], ref1)
        self.assertEqual(read_bundle_json(self.paths, 1, 2, "generation.json")["visual_input"], ref2)
        self.assertEqual(read_bundle_json(self.paths, 1, 2, "generation.json")["prompt_version"], 2)

    def test_F_candidate_visual_is_independent_from_approved(self):
        ref1, _ = self.import_ref("approved.png", 70)
        ref2, _ = self.import_ref("candidate.png", 80)
        self.generate(1, ref1)
        self.checkpoint.approve_shot(1)
        self.checkpoint.begin_candidate_editing(1, None)
        self.checkpoint.set_candidate_visual_input(1, ref2)
        self.checkpoint.candidate_checkpoint(1)["prompt_version"] = 1
        self.checkpoint.save()
        self.checkpoint.prepare_candidate_generation(1)
        self.checkpoint.mark_candidate_submitted(1, "candidate-task")
        self.assertEqual(self.checkpoint.approved_visual_input(1), ref1)
        self.assertEqual(self.checkpoint.candidate_checkpoint(1)["visual_input"], ref2)
        self.assertEqual(read_bundle_json(self.paths, 1, 2, "generation.json")["visual_input"], ref2)

    def test_G_rejected_candidate_bundle_retains_visual_snapshot(self):
        self.test_F_candidate_visual_is_independent_from_approved()
        self.paths.shot_version_video_path(1, 2).write_bytes(b"candidate")
        self.checkpoint.mark_candidate_ready(1)
        self.checkpoint.finish_candidate(1, "REJECTED")
        self.assertEqual(self.checkpoint.shot_status(1), ShotStatus.APPROVED)
        self.assertEqual(
            read_bundle_json(self.paths, 1, 2, "generation.json")["visual_input"]["mode"],
            "first_frame",
        )

    def test_H_i2v_resume_reuses_task_without_resubmit(self):
        visual, _ = self.import_ref("resume.png", 90)
        output = self.paths.shot_version_video_path(1, 1)
        output.parent.mkdir(parents=True, exist_ok=True)

        registry = create_default_registry({"minimax": "mock"})
        adapter = registry.adapter("minimax", "MiniMax-Hailuo-2.3")

        def fake_download(task, output_path, request, task_logger=None):
            output_path.write_bytes(b"resumed")
            return DownloadResult(output_path, output_path.stat().st_size)

        resume_task = ProviderTask(
            provider="minimax",
            model="MiniMax-Hailuo-2.3",
            api_version="v1",
            generation_mode="first_frame",
            provider_task_id="task-existing",
        )

        with (
            patch.object(adapter, "submit", side_effect=AssertionError("must not submit")) as submit,
            patch.object(
                adapter,
                "poll",
                return_value=resume_task.evolve(
                    provider_file_id="file-existing",
                    status=ProviderTaskStatus.COMPLETED,
                ),
            ),
            patch.object(adapter, "download", side_effect=fake_download),
        ):
            generate_video(
                {"minimax": "mock-key"},
                "prompt",
                self.paths,
                output_path=output,
                visual_input=visual,
                provider_registry=registry,
                resume_task=resume_task,
            )
        submit.assert_not_called()

    def test_I_internal_copy_survives_original_deletion(self):
        visual, original = self.import_ref("external.png", 100)
        original.unlink()
        payload = self.hailuo_payload(visual)
        self.assertIn("first_frame_image", payload)

    def test_J_history_exposes_visual_input_metadata(self):
        visual, _ = self.import_ref("history.png", 110)
        self.generate(1, visual)
        item = video_version_history(self.paths, self.checkpoint, 1)[0]
        self.assertEqual(item.visual_input_mode, "first_frame")
        self.assertEqual(item.reference_asset_ids, (visual["assets"][0]["asset_id"],))

    def test_K_old_schema2_without_visual_input_defaults_to_none(self):
        self.generate(1, none_visual_input())
        project_data = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        entry = project_data["video_generation"]["shots"]["1"]
        entry.pop("visual_input", None)
        entry.pop("visual_input_selected", None)
        for generation in entry["generation_versions"]:
            generation.pop("visual_input", None)
        self.paths.save_json(self.paths.project_state_path(), project_data)
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.shot_visual_input(1), none_visual_input())
        self.assertEqual(loaded.generation_visual_input(1, 1), none_visual_input())

    def test_L_assembly_still_reads_only_approved_bundle(self):
        visual, _ = self.import_ref("assembly.png", 120)
        for shot_id in (1, 2):
            self.generate(shot_id, visual)
            self.checkpoint.approve_shot(shot_id)
        selected = approved_shot_inputs(self.paths, self.checkpoint, self.board)
        self.assertEqual([item["shot_id"] for item in selected], [1, 2])
        self.assertTrue(all(item["path"].name == "video.mp4" for item in selected))

    def test_M_duplicate_reference_is_reused_by_sha256(self):
        visual, source = self.import_ref("dedupe.png", 130)
        duplicate = self.base / "duplicate.png"
        duplicate.write_bytes(source.read_bytes())
        second = self.assets.import_image(duplicate)
        self.assertEqual(second["asset_id"], visual["assets"][0]["asset_id"])
        self.assertEqual(len(self.assets.list_assets()), 1)

    def test_N_future_source_is_accepted_and_unimplemented_mode_is_explicit(self):
        visual, _ = self.import_ref("future.png", 140)
        visual["source"] = "image_model"
        self.assertEqual(self.assets.validate_visual_input(visual)["source"], "image_model")
        unsupported = dict(visual)
        unsupported["mode"] = "generated_keyframe"
        with self.assertRaisesRegex(VisualInputNotImplementedError, "NOT_IMPLEMENTED"):
            self.assets.validate_visual_input(unsupported)


if __name__ == "__main__":
    unittest.main()
