from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_migration import ProjectMigrationError, migrate_project_to_v2
from project_state import CandidateStatus, ProjectCheckpoint, ShotStatus
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import ReviewRecorder
from shot_manager import (
    approve_candidate,
    create_candidate_prompt_version,
    generate_candidate_video,
    reject_candidate,
)
from shot_review import create_prompt_version
from shot_storage import read_bundle_json, sha256_file, validate_bundle
from storyboard import ShotVideoPrompt, Storyboard, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_assembly import approved_shot_inputs
from video_history import switch_waiting_review_video


def safe(prompt: str, *args, **kwargs) -> PromptSafetyReview:
    return PromptSafetyReview(
        is_safe=True, risk_notes=[], reviewed_video_prompt=f"SAFE::{prompt}"
    )


class MiniMaxMock:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> Path:
        self.calls.append(dict(kwargs))
        resume_task = kwargs.get("resume_task")
        if resume_task is None:
            kwargs["on_submitted"](f"task-{len(self.calls)}")
        if resume_task is None or not resume_task.provider_file_id:
            callback = kwargs.get("on_task_updated") or kwargs.get("on_file_ready")
            callback(f"file-{len(self.calls)}")
        output = kwargs["output_path"]
        output.write_bytes(f"video-{len(self.calls)}".encode())
        return output


class ShotStorageSchemaV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "schema2")
        request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=6,
            video_style="S",
            video_purpose="U",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "P", request.model_dump()
        )
        self.request = request
        self.checkpoint.ensure_shots([1])
        self.plan = VideoPromptPlan(
            shots=[ShotVideoPrompt(shot_id=1, video_prompt="prompt-v1")]
        )
        self.board = Storyboard(
            total_duration=6,
            shots=[
                StoryboardShot(
                    shot_id=1,
                    duration=6,
                    purpose="p",
                    visual="v",
                    camera="c",
                )
            ],
        )
        self.logger = TaskLogger(self.paths, "schema2")
        create_prompt_version(
            self.paths,
            self.checkpoint,
            self.plan,
            1,
            "prompt-v1",
            "ai_generated",
            self.logger,
            parent_version=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate_normal(self, mock: MiniMaxMock) -> int:
        self.checkpoint.prepare_shot_generation(1)
        pending = int(self.checkpoint.shot_checkpoint(1)["pending_video_version"])
        output = self.paths.shot_version_video_path(1, pending)
        mock(
            shot_id=1,
            prompt="SAFE::prompt-v1",
            output_path=output,
            resume_task_id=None,
            resume_file_id=None,
            on_submitted=lambda value: self.checkpoint.mark_shot_submitted(1, value),
            on_file_ready=lambda value: self.checkpoint.mark_shot_file_ready(1, value),
        )
        self.checkpoint.mark_shot_ready_for_review(1)
        return pending

    def test_A_new_generation_is_complete_bundle(self):
        self.assertEqual(self.generate_normal(MiniMaxMock()), 1)
        summary = validate_bundle(self.paths, 1, 1)
        self.assertTrue(summary["video_exists"])
        self.assertEqual(summary["prompt"]["prompt_version"], 1)

    def test_B_regeneration_keeps_v001(self):
        mock = MiniMaxMock()
        self.generate_normal(mock)
        first_hash = sha256_file(self.paths.shot_version_video_path(1, 1))
        self.generate_normal(mock)
        self.assertEqual(sha256_file(self.paths.shot_version_video_path(1, 1)), first_hash)
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())

    def test_C_same_prompt_can_bind_two_video_versions(self):
        mock = MiniMaxMock()
        self.generate_normal(mock)
        self.generate_normal(mock)
        self.assertEqual(read_bundle_json(self.paths, 1, 1, "prompt.json")["prompt_version"], 1)
        self.assertEqual(read_bundle_json(self.paths, 1, 2, "prompt.json")["prompt_version"], 1)

    def test_D_history_switch_changes_only_pointer(self):
        mock = MiniMaxMock()
        self.generate_normal(mock)
        self.generate_normal(mock)
        before = {
            version: sha256_file(self.paths.shot_version_video_path(1, version))
            for version in (1, 2)
        }
        with patch.object(mock, "__call__", side_effect=AssertionError("no API")):
            switch_waiting_review_video(
                self.paths, self.checkpoint, self.plan, 1, 1, self.logger
            )
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["active_video_version"], 1)
        self.assertEqual(
            before,
            {
                version: sha256_file(self.paths.shot_version_video_path(1, version))
                for version in (1, 2)
            },
        )

    def _approved_v1(self) -> ReviewRecorder:
        self.generate_normal(MiniMaxMock())
        self.checkpoint.approve_shot(1)
        return ReviewRecorder(
            self.paths, self.request.model_dump(), "schema2-review", self.logger
        )

    def _candidate_v2(self, recorder: ReviewRecorder) -> MiniMaxMock:
        self.checkpoint.begin_candidate_editing(1, None)
        create_candidate_prompt_version(
            self.paths,
            self.checkpoint,
            1,
            "prompt-v1",
            "same_prompt",
            self.logger,
            parent_version=1,
        )
        self.checkpoint.prepare_candidate_generation(1)
        mock = MiniMaxMock()
        generate_candidate_video(
            self.paths,
            self.checkpoint,
            self.request,
            self.board.shots[0],
            1,
            "deepseek-mock",
            "minimax-mock",
            self.logger,
            safety_review=safe,
            video_generate=mock,
            recorder=recorder,
        )
        return mock

    def test_E_candidate_is_normal_v002_bundle(self):
        recorder = self._approved_v1()
        self._candidate_v2(recorder)
        manifest = json.loads(self.paths.shot_manifest_path(1).read_text(encoding="utf-8"))
        self.assertEqual(manifest["approved_version"], 1)
        self.assertEqual(manifest["candidate_version"], 2)
        validate_bundle(self.paths, 1, 2)

    def test_F_candidate_rejection_keeps_both_bundles(self):
        recorder = self._approved_v1()
        self._candidate_v2(recorder)
        reject_candidate(self.paths, self.checkpoint, recorder, self.logger, 1)
        manifest = json.loads(self.paths.shot_manifest_path(1).read_text(encoding="utf-8"))
        self.assertEqual(manifest["approved_version"], 1)
        self.assertIsNone(manifest["candidate_version"])
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())
        self.assertEqual(read_bundle_json(self.paths, 1, 2, "review.json")["review_result"], "REJECTED")

    def test_G_candidate_approval_changes_pointer_only(self):
        recorder = self._approved_v1()
        self._candidate_v2(recorder)
        approve_candidate(
            self.paths, self.checkpoint, self.plan, recorder, self.logger, 1
        )
        manifest = json.loads(self.paths.shot_manifest_path(1).read_text(encoding="utf-8"))
        self.assertEqual(manifest["approved_version"], 2)
        self.assertTrue(self.paths.shot_version_video_path(1, 1).is_file())

    def test_H_candidate_resume_reads_task_id_from_generation_json(self):
        recorder = self._approved_v1()
        self.checkpoint.begin_candidate_editing(1, None)
        create_candidate_prompt_version(
            self.paths, self.checkpoint, 1, "candidate", "same_prompt", self.logger, parent_version=1
        )
        self.checkpoint.prepare_candidate_generation(1)
        self.checkpoint.mark_candidate_submitted(1, "resume-task")
        data = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        data["video_generation"]["shots"]["1"]["candidate"]["provider_task_id"] = None
        self.paths.save_json(self.paths.project_state_path(), data)
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.candidate_checkpoint(1)["provider_task_id"], "resume-task")
        self.assertEqual(loaded.candidate_status(1), CandidateStatus.GENERATING)

    def test_I_assembly_uses_manifest_approved_version(self):
        self.generate_normal(MiniMaxMock())
        self.checkpoint.approve_shot(1)
        selected = approved_shot_inputs(self.paths, self.checkpoint, self.board)
        self.assertEqual(selected[0]["path"], self.paths.shot_version_video_path(1, 1))

    def _make_legacy(self, root: Path) -> ProjectPaths:
        paths = create_project_paths(root)
        state = json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))
        state.pop("project_schema_version", None)
        state["schema_version"] = 1
        state["video_generation"]["shots"] = {
            "1": {
                "status": "WAITING_REVIEW",
                "generation_count": 2,
                "active_prompt_version": 2,
                "active_video_version": 2,
                "approved_prompt_version": None,
                "approved_video_version": None,
                "provider_task_id": "task-2",
                "file_id": "file-2",
                "generation_versions": [
                    {"video_version": 1, "prompt_version": 1, "status": "WAITING_REVIEW", "provider_task_id": "task-1", "file_id": "file-1", "video_path": "shots/versions/shot_01_v001.mp4"},
                    {"video_version": 2, "prompt_version": 2, "status": "WAITING_REVIEW", "provider_task_id": "task-2", "file_id": "file-2", "video_path": "shots/shot_01.mp4"},
                ],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        paths.save_json(paths.project_state_path(), state)
        legacy_versions = paths.shots_dir / "versions"
        legacy_versions.mkdir(parents=True, exist_ok=True)
        (legacy_versions / "shot_01_v001.mp4").write_bytes(b"legacy-v1")
        (paths.shots_dir / "shot_01.mp4").write_bytes(b"legacy-v2")
        prompt_versions = paths.project_path / "prompts" / "versions"
        prompt_versions.mkdir(parents=True, exist_ok=True)
        for version, source in ((1, "ai_generated"), (2, "manual_edit")):
            paths.save_json(prompt_versions / f"shot_01_prompt_v{version:03d}.json", {"shot_id": 1, "version": version, "source": source, "prompt": f"prompt-{version}"})
        paths.save_json(paths.storyboard_file_path(), self.board.model_dump())
        return paths

    def test_J_legacy_migration_preserves_mapping(self):
        legacy = self._make_legacy(Path(self.temp.name) / "legacy")
        result = migrate_project_to_v2(legacy)
        loaded = ProjectCheckpoint.load(legacy)
        entry = loaded.shot_checkpoint(1)
        self.assertTrue(result.sha256_verified)
        self.assertEqual(entry["active_video_version"], 2)
        self.assertEqual(entry["provider_task_id"], "task-2")
        self.assertEqual(read_bundle_json(legacy, 1, 1, "prompt.json")["prompt_source"], "ai_generated")
        self.assertEqual(read_bundle_json(legacy, 1, 2, "generation.json")["file_id"], "file-2")

    def test_K_validation_failure_leaves_legacy_live(self):
        legacy = self._make_legacy(Path(self.temp.name) / "legacy-fail")
        before = (legacy.shots_dir / "shot_01.mp4").read_bytes()
        with patch("project_migration._validate_staging", side_effect=ProjectMigrationError("mock validation failure")):
            with self.assertRaises(ProjectMigrationError):
                migrate_project_to_v2(legacy)
        saved = json.loads(legacy.project_state_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual((legacy.shots_dir / "shot_01.mp4").read_bytes(), before)

    def test_L_project_manager_has_no_legacy_storage_api(self):
        for name in (
            "shot_video_path",
            "shot_versions_dir",
            "shot_candidates_dir",
            "candidate_video_path",
            "prompt_version_path",
        ):
            self.assertFalse(hasattr(self.paths, name), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
