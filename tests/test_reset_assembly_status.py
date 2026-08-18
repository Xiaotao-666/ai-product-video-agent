from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from project_manager import create_project_paths
from project_state import (
    AssemblyStatus,
    ProjectCheckpoint,
    ProjectStage,
    STAGE_ORDER,
    StageStatus,
    display_project_status,
)
from prompt_generator import ProductVideoRequest


class ResetAssemblyStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def completed_project(self, name: str = "project"):
        paths = create_project_paths(Path(self.temp.name) / name)
        request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=6,
            video_style="S",
            video_purpose="U",
        )
        checkpoint = ProjectCheckpoint.create(paths, "P", request.model_dump())
        for stage in STAGE_ORDER:
            checkpoint.data["stages"][stage.value]["status"] = (
                StageStatus.COMPLETED.value
            )
        checkpoint.data["current_stage"] = ProjectStage.COMPLETED.value
        checkpoint.data["status"] = StageStatus.COMPLETED.value
        final = paths.final_video_path()
        final.write_bytes(b"historical-final-video")
        checkpoint.assembly_checkpoint().update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "changed_shot_id": None,
                "old_approved_video_version": None,
                "new_approved_video_version": None,
                "final_video_path": "videos/final_video.mp4",
                "final_video_version": 1,
                "assembled_at": "2026-08-14T10:00:00",
                "total_duration": 6.0,
            }
        )
        checkpoint.save()
        return paths, checkpoint, final

    @staticmethod
    def display(checkpoint: ProjectCheckpoint) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            display_project_status(checkpoint)
        return output.getvalue()

    def test_A_completed_reset_creative_accepts_null_changed_shot(self):
        _paths, checkpoint, _final = self.completed_project("A")
        checkpoint.reset_from(ProjectStage.CREATIVE)
        assembly = checkpoint.assembly_checkpoint()
        self.assertIsNone(assembly["changed_shot_id"])
        self.assertIn("工作流重置后需要重新合片", self.display(checkpoint))

    def test_B_no_update_and_null_changed_shot_displays_normally(self):
        _paths, checkpoint, _final = self.completed_project("B")
        assembly = checkpoint.assembly_checkpoint()
        assembly["needs_update"] = False
        assembly["changed_shot_id"] = None
        checkpoint.save()
        output = self.display(checkpoint)
        self.assertNotIn("Shot None", output)
        self.assertNotIn("需要重新合片", output)

    def test_C_specific_changed_shot_formats_as_two_digits(self):
        _paths, checkpoint, _final = self.completed_project("C")
        assembly = checkpoint.assembly_checkpoint()
        assembly.update(
            {
                "needs_update": True,
                "changed_shot_id": 2,
                "old_approved_video_version": 1,
                "new_approved_video_version": 3,
            }
        )
        checkpoint.save()
        output = self.display(checkpoint)
        self.assertIn("Shot 02 已更新", output)
        self.assertIn("v1 → v3", output)

    def test_D_all_optional_assembly_fields_may_be_none(self):
        _paths, checkpoint, _final = self.completed_project("D")
        assembly = checkpoint.assembly_checkpoint()
        for field in (
            "changed_shot_id",
            "old_approved_video_version",
            "new_approved_video_version",
            "final_video_version",
            "final_video_path",
            "assembled_at",
            "total_duration",
            "pending_final_video_version",
            "pending_final_video_path",
        ):
            assembly[field] = None
        assembly["status"] = AssemblyStatus.NOT_STARTED.value
        assembly["needs_update"] = True
        checkpoint.save()
        output = self.display(checkpoint)
        self.assertIn("暂无变更镜头", output)

    def test_E_reset_creative_reload_stays_at_creative_without_second_reset(self):
        paths, checkpoint, _final = self.completed_project("E")
        checkpoint.reset_from(ProjectStage.CREATIVE)
        revision_count = len(checkpoint.data["revision_history"])
        loaded = ProjectCheckpoint.load(paths)
        self.assertEqual(loaded.current_stage, ProjectStage.CREATIVE)
        self.assertEqual(loaded.status, StageStatus.NOT_STARTED.value)
        self.display(loaded)
        self.assertEqual(len(loaded.data["revision_history"]), revision_count)

    def test_F_reset_never_deletes_historical_final_video(self):
        _paths, checkpoint, final = self.completed_project("F")
        before = final.read_bytes()
        checkpoint.reset_from(ProjectStage.CREATIVE)
        self.assertTrue(final.is_file())
        self.assertEqual(final.read_bytes(), before)

    def test_G_all_supported_restart_stages_display_safely(self):
        for stage in (
            ProjectStage.STORYBOARD,
            ProjectStage.VIDEO_PROMPT,
            ProjectStage.VIDEO_GENERATION,
        ):
            with self.subTest(stage=stage.value):
                _paths, checkpoint, final = self.completed_project(stage.value)
                checkpoint.reset_from(stage)
                self.assertEqual(checkpoint.current_stage, stage)
                self.assertEqual(checkpoint.status, StageStatus.NOT_STARTED.value)
                self.assertIn("项目状态", self.display(checkpoint))
                self.assertTrue(final.is_file())


if __name__ == "__main__":
    unittest.main()
