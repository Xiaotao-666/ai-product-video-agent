from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from project_manager import ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint
from shot_approval_workflow import ShotApprovalError, approve_shot_stage
from shot_storage import ensure_bundle_placeholders, sync_shot_manifest_from_checkpoint
from tests.web.test_backend_phase_1b_projects import base_project


class ShotApprovalWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths, self.checkpoint = self._fixture(Path(self.temp.name) / "project")

    @staticmethod
    def _fixture(root: Path) -> tuple[ProjectPaths, ProjectCheckpoint]:
        paths = create_project_paths(root)
        project = base_project(project_id="project-a", project_name="Shot approve")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        entry = {
            "shot_id": 1,
            "status": "WAITING_REVIEW",
            "generation_phase": "WAITING_REVIEW",
            "generation_count": 1,
            "active_prompt_version": 2,
            "approved_prompt_version": None,
            "active_video_version": 1,
            "approved_video_version": None,
            "pending_video_version": None,
            "current_generation_version": 1,
            "submission_unknown": False,
            "prompt_versions": [
                {
                    "shot_id": 1,
                    "version": 2,
                    "prompt": "approved active prompt",
                    "source": "ai_revision",
                }
            ],
            "generation_versions": [
                {
                    "video_version": 1,
                    "prompt_version": 2,
                    "status": "WAITING_REVIEW",
                    "review_result": "WAITING_REVIEW",
                    "generation_phase": "WAITING_REVIEW",
                    "provider": "minimax",
                    "provider_model": "MiniMax-Hailuo-2.3",
                    "generation_mode": "text_to_video",
                    "generation_count": 1,
                    "prompt_snapshot": {
                        "version": 2,
                        "prompt": "approved active prompt",
                        "source": "ai_revision",
                    },
                }
            ],
            "candidate": {"status": "NONE", "video_version": None},
        }
        project["video_generation"]["shots"] = {"1": entry}
        paths.save_json(paths.project_state_path(), project)
        ensure_bundle_placeholders(
            paths,
            1,
            1,
            prompt_payload={
                "version": 2,
                "prompt": "approved active prompt",
                "source": "ai_revision",
            },
            generation_payload={
                "video_version": 1,
                "prompt_version": 2,
                "status": "WAITING_REVIEW",
                "generation_phase": "WAITING_REVIEW",
                "provider": "minimax",
                "provider_model": "MiniMax-Hailuo-2.3",
                "generation_mode": "text_to_video",
                "generation_count": 1,
            },
            review_result="WAITING_REVIEW",
        )
        paths.shot_version_video_path(1, 1).write_bytes(b"mock-mp4-content")
        checkpoint = ProjectCheckpoint.load(paths)
        sync_shot_manifest_from_checkpoint(paths, 1, checkpoint.shot_checkpoint(1))
        return paths, checkpoint

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_approve_preserves_media_snapshots_and_records_cli_metadata(self) -> None:
        immutable = [
            self.paths.shot_version_video_path(1, 1),
            self.paths.shot_version_prompt_path(1, 1),
            self.paths.shot_version_safety_path(1, 1),
            self.paths.shot_version_generation_path(1, 1),
        ]
        before = {path: (self._digest(path), path.stat().st_mtime_ns) for path in immutable}
        review_before = json.loads(
            self.paths.shot_version_review_path(1, 1).read_text(encoding="utf-8")
        )
        recorder = Mock()
        logger = Mock()

        approved = approve_shot_stage(
            paths=self.paths,
            checkpoint=self.checkpoint,
            shot_id=1,
            recorder=recorder,
            task_logger=logger,
        )

        self.assertEqual(approved, 1)
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["status"], "APPROVED")
        self.assertEqual(entry["active_video_version"], 1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["active_prompt_version"], 2)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["candidate"]["status"], "NONE")
        self.assertEqual(entry["generation_versions"][0]["review_result"], "APPROVED")
        self.assertEqual(entry["generation_versions"][0]["is_approved"], True)
        for path, snapshot in before.items():
            self.assertEqual((self._digest(path), path.stat().st_mtime_ns), snapshot)

        review = json.loads(
            self.paths.shot_version_review_path(1, 1).read_text(encoding="utf-8")
        )
        self.assertEqual(review["review_result"], "APPROVED")
        self.assertEqual(review["user_action"], "approve")
        self.assertEqual(review["history"][:-1], review_before["history"])
        self.assertEqual(review["history"][-1]["review_result"], "APPROVED")
        recorder.record_shot_action.assert_called_once_with(
            1, "approve", prompt_version=2, video_version=1
        )
        logger.event.assert_called_once_with(
            "SHOT_REVIEW_APPROVED",
            shot_id=1,
            approved_prompt_version=2,
            approved_video_version=1,
            generation_count=1,
        )

    def test_invalid_state_or_incomplete_bundle_is_rejected_without_writes(self) -> None:
        project_before = self.paths.project_state_path().read_bytes()
        self.checkpoint.shot_checkpoint(1)["status"] = "FAILED"
        with self.assertRaises(ShotApprovalError):
            approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        self.assertEqual(self.paths.project_state_path().read_bytes(), project_before)

        self.checkpoint = ProjectCheckpoint.load(self.paths)
        self.paths.shot_version_video_path(1, 1).unlink()
        with self.assertRaises(ShotApprovalError):
            approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        persisted = ProjectCheckpoint.load(self.paths).shot_checkpoint(1)
        self.assertEqual(persisted["status"], "WAITING_REVIEW")
        self.assertIsNone(persisted["approved_video_version"])

    def test_approved_shot_is_not_approvable_twice(self) -> None:
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        with self.assertRaises(ShotApprovalError):
            approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)


if __name__ == "__main__":
    unittest.main()
