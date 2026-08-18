from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import (
    AssemblyStatus,
    CandidateStatus,
    ProjectCheckpoint,
    ShotStatus,
)
from prompt_generator import ProductVideoRequest
from storyboard import Storyboard, StoryboardShot
from task_logger import TaskLogger
from video_assembly import (
    AssemblyError,
    _load_manifest,
    approved_shot_inputs,
    assemble_approved_shots,
    assembly_menu,
    detect_ffmpeg_tools,
    probe_media,
    select_final_output,
)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "FFmpeg/FFprobe required")
class VideoAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths, self.checkpoint, self.board, self.logger = self.make_project(
            "project", 3
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_project(self, name: str, count: int):
        paths = create_project_paths(Path(self.temp.name) / name)
        request = ProductVideoRequest(
            product_name=name,
            product_description="本地测试",
            duration_seconds=count * 6,
            video_style="测试",
            video_purpose="测试",
        )
        checkpoint = ProjectCheckpoint.create(paths, name, request.model_dump())
        board = Storyboard(
            total_duration=count * 6,
            shots=[
                StoryboardShot(
                    shot_id=shot_id,
                    duration=6,
                    purpose=f"purpose-{shot_id}",
                    visual=f"visual-{shot_id}",
                    camera=f"camera-{shot_id}",
                )
                for shot_id in range(1, count + 1)
            ],
        )
        paths.save_json(paths.storyboard_file_path(), board.model_dump())
        checkpoint.ensure_shots([shot.shot_id for shot in board.shots])
        for shot in board.shots:
            entry = checkpoint.shot_checkpoint(shot.shot_id)
            entry.update(
                {
                    "status": ShotStatus.APPROVED.value,
                    "active_video_version": 1,
                    "approved_video_version": 1,
                    "active_prompt_version": 1,
                    "approved_prompt_version": 1,
                    "generation_count": 1,
                }
            )
        checkpoint.data["video_generation"]["completed_shots"] = list(
            range(1, count + 1)
        )
        checkpoint.save()
        return paths, checkpoint, board, TaskLogger(paths, f"assembly-{name}")

    def make_video(
        self,
        path: Path,
        *,
        color: str = "red",
        size: str = "320x240",
        fps: int = 24,
        duration: float = 0.35,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                str(FFMPEG),
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}:r={fps}:d={duration}",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def fill_matching_videos(self) -> None:
        for shot_id, color in enumerate(("red", "green", "blue"), 1):
            self.make_video(self.paths.shot_version_video_path(shot_id, 1), color=color)

    def assemble(self, paths=None, checkpoint=None, board=None, logger=None):
        paths = paths or self.paths
        checkpoint = checkpoint or self.checkpoint
        board = board or self.board
        logger = logger or self.logger
        return assemble_approved_shots(
            paths,
            checkpoint,
            board,
            logger,
            output_selection=(paths.final_video_path(), 1),
        )

    def final_info(self, path: Path):
        return probe_media(
            str(FFPROBE), 0, path, self.paths, self.logger
        )

    def test_A_matching_approved_shots_use_fast_concat(self):
        self.fill_matching_videos()
        final = self.assemble()
        self.assertEqual(final, self.paths.final_video_path())
        self.assertTrue(final.is_file())
        manifest = _load_manifest(self.paths)
        latest = manifest["assemblies"][-1]
        self.assertEqual(latest["mode"], "concat_copy")
        self.assertEqual([item["shot_id"] for item in latest["shots"]], [1, 2, 3])
        self.assertEqual(self.checkpoint.assembly_checkpoint()["status"], "COMPLETED")
        self.assertFalse(self.checkpoint.assembly_checkpoint()["needs_update"])

    def test_B_different_fps_is_normalized(self):
        self.make_video(self.paths.shot_version_video_path(1, 1), fps=24)
        self.make_video(self.paths.shot_version_video_path(2, 1), fps=30)
        self.make_video(self.paths.shot_version_video_path(3, 1), fps=24)
        final = self.assemble()
        manifest = _load_manifest(self.paths)["assemblies"][-1]
        self.assertEqual(manifest["mode"], "normalized_concat")
        self.assertAlmostEqual(self.final_info(final).fps, 24.0, places=2)

    def test_C_different_resolution_uses_scale_pad_without_stretch(self):
        self.make_video(self.paths.shot_version_video_path(1, 1), size="320x240")
        self.make_video(self.paths.shot_version_video_path(2, 1), size="640x360")
        self.make_video(self.paths.shot_version_video_path(3, 1), size="240x320")
        commands: list[list[str]] = []

        def recording_runner(command, **kwargs):
            commands.append(command)
            return subprocess.run(command, **kwargs)

        final = assemble_approved_shots(
            self.paths,
            self.checkpoint,
            self.board,
            self.logger,
            output_selection=(self.paths.final_video_path(), 1),
            runner=recording_runner,
        )
        info = self.final_info(final)
        self.assertEqual((info.width, info.height), (320, 240))
        filters = [
            command[command.index("-vf") + 1]
            for command in commands
            if "-vf" in command
        ]
        self.assertTrue(filters)
        self.assertTrue(
            all("force_original_aspect_ratio=decrease" in value for value in filters)
        )
        self.assertTrue(all("pad=320:240" in value for value in filters))
        log = self.logger.task_log_path.read_text(encoding="utf-8")
        self.assertIn("VIDEO_NORMALIZATION_STARTED", log)

    def test_D_non_approved_shot_blocks_assembly(self):
        self.fill_matching_videos()
        self.checkpoint.shot_checkpoint(3)["status"] = ShotStatus.WAITING_REVIEW.value
        self.checkpoint.save()
        with self.assertRaisesRegex(AssemblyError, "Shot 03：WAITING_REVIEW"):
            self.assemble()
        self.assertFalse(self.paths.final_video_path().exists())

    def test_E_missing_approved_file_names_shot(self):
        self.make_video(self.paths.shot_version_video_path(1, 1))
        self.make_video(self.paths.shot_version_video_path(2, 1))
        with self.assertRaisesRegex(AssemblyError, "Shot 03 无法正常读取"):
            self.assemble()
        self.assertEqual(
            self.checkpoint.assembly_checkpoint()["status"],
            AssemblyStatus.FAILED.value,
        )

    def test_F_corrupt_video_is_rejected_by_ffprobe(self):
        self.fill_matching_videos()
        self.paths.shot_version_video_path(2, 1).write_bytes(b"not-a-video")
        with self.assertRaisesRegex(AssemblyError, "Shot 02 无法正常读取"):
            self.assemble()

    def test_G_existing_final_is_never_silently_overwritten(self):
        self.paths.final_video_path().write_bytes(b"existing-final")
        with patch("builtins.input", side_effect=[""]):
            selected = select_final_output(self.paths)
        self.assertEqual(selected, (self.paths.final_video_version_path(2), 2))
        self.assertEqual(self.paths.final_video_path().read_bytes(), b"existing-final")
        with patch("builtins.input", side_effect=["3"]):
            self.assertIsNone(select_final_output(self.paths))

    def test_H_ffmpeg_failure_marks_assembly_failed_and_keeps_shots_approved(self):
        self.fill_matching_videos()
        with patch(
            "video_assembly._concat_copy",
            side_effect=AssemblyError("mock ffmpeg failed return code 9"),
        ), patch(
            "video_assembly._normalize_and_concat",
            side_effect=AssemblyError("mock ffmpeg failed return code 9"),
        ):
            with self.assertRaisesRegex(AssemblyError, "return code 9"):
                self.assemble()
        self.assertEqual(
            self.checkpoint.assembly_checkpoint()["status"],
            AssemblyStatus.FAILED.value,
        )
        self.assertTrue(self.checkpoint.all_shots_approved([1, 2, 3]))

    def test_I_completed_resume_does_not_automatically_run_ffmpeg(self):
        self.fill_matching_videos()
        final = self.assemble()
        forbidden = Mock(side_effect=AssertionError("Resume must not run FFmpeg"))
        with patch("video_assembly.assemble_approved_shots", forbidden), patch(
            "builtins.input", side_effect=["4"]
        ):
            result = assembly_menu(
                self.paths,
                self.checkpoint,
                self.board,
                self.logger,
                open_shot_management=Mock(),
            )
        self.assertEqual(result, final)
        forbidden.assert_not_called()

    def test_running_resume_does_not_accept_an_older_manifest(self):
        self.fill_matching_videos()
        final = self.assemble()
        manifest_before = _load_manifest(self.paths)
        pending_path = self.paths.final_video_version_path(2)
        pending_shots = [
            {
                "shot_id": shot_id,
                "approved_video_version": 1,
                "video_path": f"shots/shot_{shot_id:02d}/v001/video.mp4",
            }
            for shot_id in (1, 2, 3)
        ]
        self.checkpoint.start_assembly(pending_path, 2, pending_shots)
        forbidden = Mock(side_effect=AssertionError("return menu must not run FFmpeg"))
        with patch("video_assembly.assemble_approved_shots", forbidden), patch(
            "builtins.input", side_effect=["2"]
        ):
            result = assembly_menu(
                self.paths,
                self.checkpoint,
                self.board,
                self.logger,
                open_shot_management=Mock(),
            )
        self.assertIsNone(result)
        self.assertEqual(
            self.checkpoint.assembly_checkpoint()["status"],
            AssemblyStatus.RUNNING.value,
        )
        self.assertEqual(_load_manifest(self.paths), manifest_before)
        self.assertEqual(final, self.paths.final_video_path())
        forbidden.assert_not_called()

    def test_J_shot_update_keeps_old_final_and_marks_outdated(self):
        self.fill_matching_videos()
        final = self.assemble()
        old_bytes = final.read_bytes()
        self.checkpoint.mark_assembly_needs_update(2, 1, 2)
        self.make_video(self.paths.shot_version_video_path(2, 2), color="yellow")
        self.checkpoint.shot_checkpoint(2)["approved_video_version"] = 2
        self.checkpoint.save()
        self.assertEqual(final.read_bytes(), old_bytes)
        assembly = self.checkpoint.assembly_checkpoint()
        self.assertTrue(assembly["needs_update"])
        self.assertEqual(assembly["changed_shot_id"], 2)

    def test_K_projects_keep_work_manifest_video_and_logs_isolated(self):
        self.fill_matching_videos()
        final_a = self.assemble()
        paths_b, checkpoint_b, board_b, logger_b = self.make_project("project-b", 2)
        self.make_video(paths_b.shot_version_video_path(1, 1), color="yellow")
        self.make_video(paths_b.shot_version_video_path(2, 1), color="purple")
        final_b = self.assemble(paths_b, checkpoint_b, board_b, logger_b)
        self.assertNotEqual(final_a, final_b)
        self.assertTrue(self.paths.assembly_manifest_path().is_file())
        self.assertTrue(paths_b.assembly_manifest_path().is_file())
        self.assertTrue(str(self.logger.task_log_path).startswith(str(self.paths.project_path)))
        self.assertTrue(str(logger_b.task_log_path).startswith(str(paths_b.project_path)))
        self.assertNotIn(paths_b.project_path.resolve(), final_a.resolve().parents)
        self.assertNotIn(self.paths.project_path.resolve(), final_b.resolve().parents)

    def test_L_unapproved_candidate_never_participates(self):
        self.fill_matching_videos()
        candidate_path = self.paths.shot_version_video_path(2, 2)
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_bytes(b"corrupt-unapproved-candidate")
        candidate = self.checkpoint.candidate_checkpoint(2)
        candidate.update(
            {
                "status": CandidateStatus.WAITING_REVIEW.value,
                "video_version": 2,
                "video_path": candidate_path.resolve().relative_to(
                    self.paths.project_path.resolve()
                ).as_posix(),
            }
        )
        self.checkpoint.save()
        final = self.assemble()
        self.assertTrue(final.is_file())
        manifest = _load_manifest(self.paths)["assemblies"][-1]
        shot_two = next(item for item in manifest["shots"] if item["shot_id"] == 2)
        self.assertEqual(shot_two["approved_video_version"], 1)
        self.assertEqual(shot_two["video_path"], "shots/shot_02/v001/video.mp4")
        self.assertTrue(candidate_path.is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
