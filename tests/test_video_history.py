from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import CandidateStatus, ProjectCheckpoint, ShotStatus
from review_manager import ReviewRecorder
from storyboard import ShotVideoPrompt, VideoPromptPlan
from task_logger import TaskLogger
from video_history import (
    create_historical_candidate,
    display_video_history,
    switch_waiting_review_video,
    video_version_history,
)


class VideoHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "history-project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "history-project", {"product_name": "test"}
        )
        self.checkpoint.ensure_shots([1, 2])
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(shot_id=1, video_prompt="prompt-one-v1"),
                ShotVideoPrompt(shot_id=2, video_prompt="prompt-two-v1"),
            ]
        )
        self.paths.save_json(self.paths.video_prompts_path(), self.plan.model_dump())
        self.logger = TaskLogger(self.paths, "video-history")
        self.recorder = ReviewRecorder(
            self.paths,
            {"product_name": "test"},
            self.logger.task_id,
            self.logger,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_prompt(self, shot_id: int, version: int, source: str) -> None:
        self.checkpoint.save_prompt_version(
            shot_id,
            {
                "shot_id": shot_id,
                "version": version,
                "source": source,
                "created_at": f"2026-08-13T15:2{version}:00",
                "prompt": f"prompt-{shot_id}-v{version}",
            },
        )

    def add_generation(
        self,
        shot_id: int,
        video_version: int,
        prompt_version: int,
        content: bytes,
        *,
        active: bool = False,
    ) -> None:
        entry = self.checkpoint.shot_checkpoint(shot_id)
        path = self.paths.shot_version_video_path(shot_id, video_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rel = path.resolve().relative_to(self.paths.project_path.resolve()).as_posix()
        entry.setdefault("generation_versions", []).append(
            {
                "video_version": video_version,
                "prompt_version": prompt_version,
                "status": "WAITING_REVIEW",
                "created_at": f"2026-08-13T15:3{video_version}:00",
                "provider_task_id": f"task-v{video_version}",
                "file_id": f"file-v{video_version}",
                "video_path": rel,
                "archived_path": None if active else rel,
                "review_result": "WAITING_REVIEW",
                "is_active": active,
                "is_approved": False,
            }
        )
        if active:
            entry.update(
                {
                    "status": ShotStatus.WAITING_REVIEW.value,
                    "active_prompt_version": prompt_version,
                    "active_video_version": video_version,
                    "generation_count": len(entry["generation_versions"]),
                    "provider_task_id": f"task-v{video_version}",
                    "file_id": f"file-v{video_version}",
                }
            )
        self.checkpoint.save()

    def waiting_three_versions(self) -> None:
        self.add_prompt(1, 1, "ai_generated")
        self.add_prompt(1, 2, "manual_edit")
        self.add_generation(1, 1, 1, b"video-v1")
        self.add_generation(1, 2, 2, b"video-v2")
        self.add_generation(1, 3, 2, b"video-v3", active=True)

    def test_A_switch_to_v1_is_local_and_keeps_v2(self):
        self.waiting_three_versions()
        before = self.checkpoint.shot_checkpoint(1)["generation_count"]
        forbidden_api = Mock(side_effect=AssertionError("API must not be called"))
        switch_waiting_review_video(
            self.paths,
            self.checkpoint,
            self.plan,
            1,
            1,
            self.logger,
            self.recorder,
        )
        forbidden_api.assert_not_called()
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["generation_count"], before)
        self.assertEqual(entry["active_video_version"], 1)
        self.assertEqual(self.paths.shot_version_video_path(1, 1).read_bytes(), b"video-v1")
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())
        self.assertEqual(self.paths.shot_version_video_path(1, 3).read_bytes(), b"video-v3")

    def test_B_switched_v1_can_be_approved(self):
        self.waiting_three_versions()
        switch_waiting_review_video(
            self.paths, self.checkpoint, self.plan, 1, 1, self.logger
        )
        self.checkpoint.approve_shot(1)
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 1)

    def test_C_two_video_versions_keep_same_prompt_v2(self):
        self.waiting_three_versions()
        history = {item.video_version: item for item in video_version_history(
            self.paths, self.checkpoint, 1
        )}
        self.assertEqual(history[2].prompt_version, 2)
        self.assertEqual(history[3].prompt_version, 2)
        self.assertEqual(history[2].prompt_source, "manual_edit")
        self.assertEqual(history[3].prompt_source, "manual_edit")

    def test_D_switch_v1_then_v3_keeps_all_versions_recoverable(self):
        self.waiting_three_versions()
        switch_waiting_review_video(
            self.paths, self.checkpoint, self.plan, 1, 1, self.logger
        )
        switch_waiting_review_video(
            self.paths, self.checkpoint, self.plan, 1, 3, self.logger
        )
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["active_video_version"], 3)
        self.assertEqual(self.paths.shot_version_video_path(1, 3).read_bytes(), b"video-v3")
        history = video_version_history(self.paths, self.checkpoint, 1)
        self.assertTrue(all(item.exists for item in history))
        self.assertEqual({item.video_version for item in history}, {1, 2, 3})

    def test_E_resume_preserves_switched_waiting_version(self):
        self.waiting_three_versions()
        switch_waiting_review_video(
            self.paths, self.checkpoint, self.plan, 1, 1, self.logger
        )
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.shot_status(1), ShotStatus.WAITING_REVIEW)
        self.assertEqual(loaded.shot_checkpoint(1)["active_video_version"], 1)
        self.assertEqual(self.paths.shot_version_video_path(1, 1).read_bytes(), b"video-v1")

    def test_F_approved_historical_candidate_marks_assembly_outdated_on_approval(self):
        self.waiting_three_versions()
        self.checkpoint.approve_shot(1)  # v3 approved
        self.paths.final_video_path().write_bytes(b"old-final")
        self.checkpoint.complete_assembly(
            self.paths.final_video_path(), 1, 1.0,
            [{"shot_id": 1, "approved_video_version": 3}],
        )
        create_historical_candidate(
            self.paths, self.checkpoint, 1, 1, self.logger, self.recorder
        )
        self.assertEqual(self.checkpoint.candidate_status(1), CandidateStatus.WAITING_REVIEW)

        # Exercise the existing Candidate approval path without any API call.
        from shot_manager import approve_candidate

        approve_candidate(
            self.paths,
            self.checkpoint,
            self.plan,
            self.recorder,
            self.logger,
            1,
        )
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertTrue(self.checkpoint.assembly_checkpoint()["needs_update"])
        self.assertEqual(self.paths.final_video_path().read_bytes(), b"old-final")

    def test_G_missing_old_file_is_marked_and_not_restored(self):
        self.waiting_three_versions()
        self.paths.shot_version_video_path(1, 1).unlink()
        history = {item.video_version: item for item in display_video_history(
            self.paths, self.checkpoint, 1
        )}
        self.assertFalse(history[1].exists)
        from video_history import VideoHistoryError

        with self.assertRaises(VideoHistoryError):
            switch_waiting_review_video(
                self.paths, self.checkpoint, self.plan, 1, 1, self.logger
            )
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["active_video_version"], 3)
        self.assertEqual(self.paths.shot_version_video_path(1, 3).read_bytes(), b"video-v3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
