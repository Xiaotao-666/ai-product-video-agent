from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_manager import ProjectPaths, create_project_paths
from project_migration import (
    ProjectMigrationError,
    cleanup_legacy_schema1,
    migrate_project_to_v2,
)
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from storyboard import Storyboard, StoryboardShot
from video_assembly import approved_shot_inputs
from video_history import video_version_history


class MigrationCoexistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.paths = self._make_legacy(self.base / "legacy-project")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _make_legacy(self, root: Path) -> ProjectPaths:
        template = create_project_paths(self.base / "state-template")
        request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=12,
            video_style="S",
            video_purpose="U",
        )
        checkpoint = ProjectCheckpoint.create(
            template, "legacy", request.model_dump()
        )
        state = json.loads(template.project_state_path().read_text(encoding="utf-8"))
        state.pop("project_schema_version", None)
        state["schema_version"] = 1
        state.pop("legacy_cleanup_pending", None)
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
                    {
                        "video_version": 1,
                        "prompt_version": 1,
                        "status": "WAITING_REVIEW",
                        "provider_task_id": "task-1",
                        "file_id": "file-1",
                        "video_path": "shots/versions/shot_01_v001.mp4",
                    },
                    {
                        "video_version": 2,
                        "prompt_version": 2,
                        "status": "WAITING_REVIEW",
                        "provider_task_id": "task-2",
                        "file_id": "file-2",
                        "video_path": "shots/shot_01.mp4",
                    },
                ],
                "candidate": {"status": "NONE", "video_version": None},
            },
            "2": {
                "status": "NOT_STARTED",
                "generation_count": 0,
                "active_prompt_version": 1,
                "active_video_version": None,
                "approved_prompt_version": None,
                "approved_video_version": None,
                "provider_task_id": None,
                "file_id": None,
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            },
        }
        paths = create_project_paths(root)
        paths.save_json(paths.project_state_path(), state)
        versions = paths.shots_dir / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        (versions / "shot_01_v001.mp4").write_bytes(b"legacy-video-v1")
        (paths.shots_dir / "shot_01.mp4").write_bytes(b"legacy-video-v2")
        (paths.shots_dir / "candidates").mkdir(exist_ok=True)
        prompt_versions = root / "prompts" / "versions"
        prompt_versions.mkdir(parents=True, exist_ok=True)
        for shot_id, version, source in (
            (1, 1, "ai_generated"),
            (1, 2, "manual_edit"),
            (2, 1, "ai_generated"),
        ):
            paths.save_json(
                prompt_versions
                / f"shot_{shot_id:02d}_prompt_v{version:03d}.json",
                {
                    "shot_id": shot_id,
                    "version": version,
                    "source": source,
                    "prompt": f"prompt-{shot_id}-{version}",
                },
            )
        paths.save_json(
            root / "prompts" / "video_prompts.json",
            {
                "shots": [
                    {"shot_id": 1, "video_prompt": "prompt-1-2"},
                    {"shot_id": 2, "video_prompt": "prompt-2-1"},
                ]
            },
        )
        paths.save_json(
            paths.storyboard_file_path(),
            Storyboard(
                total_duration=12,
                shots=[
                    StoryboardShot(
                        shot_id=1, duration=6, purpose="p1", visual="v1", camera="c1"
                    ),
                    StoryboardShot(
                        shot_id=2, duration=6, purpose="p2", visual="v2", camera="c2"
                    ),
                ],
            ).model_dump(),
        )
        return paths

    def _load_state(self) -> dict:
        return json.loads(self.paths.project_state_path().read_text(encoding="utf-8"))

    def _board(self) -> Storyboard:
        return Storyboard.model_validate(
            json.loads(self.paths.storyboard_file_path().read_text(encoding="utf-8"))
        )

    def test_A_locked_legacy_shots_rename_is_not_required(self):
        original_replace = Path.replace

        def reject_legacy_shots(path: Path, target: Path):
            if path.resolve() == self.paths.shots_dir.resolve():
                raise PermissionError(13, "locked legacy shots", str(path), 5)
            return original_replace(path, target)

        with patch.object(Path, "replace", new=reject_legacy_shots):
            result = migrate_project_to_v2(self.paths)
        self.assertTrue(result.sha256_verified)
        self.assertEqual(self._load_state()["project_schema_version"], 2)
        self.assertTrue((self.paths.shots_dir / "shot_01.mp4").is_file())
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())

    def test_B_legacy_and_schema2_coexist_runtime_reads_bundle_only(self):
        migrate_project_to_v2(self.paths)
        (self.paths.shots_dir / "shot_01.mp4").write_bytes(b"wrong-legacy-active")
        checkpoint = ProjectCheckpoint.load(self.paths)
        self.assertEqual(
            checkpoint.active_video_path(1),
            self.paths.shot_version_video_path(1, 2),
        )
        self.assertEqual(
            checkpoint.active_video_path(1).read_bytes(), b"legacy-video-v2"
        )

    def test_C_mid_install_failure_keeps_schema1_and_cleans_new_paths(self):
        import project_migration

        def fail_after_first(paths, staging, migrated, copy_map, created):
            source = staging / "shots" / "shot_01"
            destination = paths.shots_dir / "shot_01"
            shutil.copytree(source, destination)
            created.append(destination)
            raise ProjectMigrationError("mock bundle install failure")

        with patch.object(project_migration, "_install_schema2_tree", fail_after_first):
            with self.assertRaises(ProjectMigrationError):
                migrate_project_to_v2(self.paths)
        state = self._load_state()
        self.assertEqual(state["schema_version"], 1)
        self.assertNotIn("project_schema_version", state)
        self.assertFalse((self.paths.shots_dir / "shot_01").exists())
        self.assertEqual(
            (self.paths.shots_dir / "shot_01.mp4").read_bytes(),
            b"legacy-video-v2",
        )
        logs = sorted(self.paths.error_logs_dir.glob("migration_*.log"))
        payload = json.loads(logs[-1].read_text(encoding="utf-8"))
        for field in (
            "exception_type",
            "errno",
            "winerror",
            "source_path",
            "destination_path",
            "operation",
            "traceback",
        ):
            self.assertIn(field, payload)

    def test_D_cleanup_lock_failure_keeps_schema2_resumable_and_pending(self):
        migrate_project_to_v2(self.paths)
        real_rmtree = shutil.rmtree

        def locked(path, *args, **kwargs):
            if Path(path).resolve() == (self.paths.shots_dir / "versions").resolve():
                raise PermissionError(13, "locked by explorer", str(path), 5)
            return real_rmtree(path, *args, **kwargs)

        with patch("project_migration.shutil.rmtree", side_effect=locked):
            result = cleanup_legacy_schema1(self.paths)
        self.assertTrue(result.cleanup_pending)
        self.assertTrue(self._load_state()["legacy_cleanup_pending"])
        checkpoint = ProjectCheckpoint.load(self.paths)
        self.assertEqual(checkpoint.shot_status(1).value, "WAITING_REVIEW")
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())

    def test_E_cleanup_success_marks_pending_false(self):
        migrate_project_to_v2(self.paths)
        result = cleanup_legacy_schema1(self.paths)
        self.assertFalse(result.cleanup_pending)
        self.assertFalse(self._load_state()["legacy_cleanup_pending"])
        self.assertFalse((self.paths.shots_dir / "shot_01.mp4").exists())
        self.assertFalse((self.paths.shots_dir / "versions").exists())
        self.assertFalse((self.paths.project_path / "prompts").exists())
        self.assertTrue(self.paths.shot_version_video_path(1, 2).is_file())

    def test_F_assembly_uses_approved_bundle_while_legacy_active_exists(self):
        migrate_project_to_v2(self.paths)
        checkpoint = ProjectCheckpoint.load(self.paths)
        entry = checkpoint.shot_checkpoint(1)
        entry["status"] = "APPROVED"
        entry["approved_video_version"] = 1
        entry["approved_prompt_version"] = 1
        checkpoint.shot_checkpoint(2)["status"] = "APPROVED"
        checkpoint.shot_checkpoint(2)["approved_video_version"] = 1
        # Give Shot 02 a real Schema 2 Bundle only for this assembly selector test.
        source = self.paths.shot_version_dir(1, 1)
        destination = self.paths.shot_version_dir(2, 1)
        shutil.copytree(source, destination)
        for name in ("prompt.json", "generation.json", "review.json"):
            payload = json.loads((destination / name).read_text(encoding="utf-8"))
            payload["shot_id"] = 2
            self.paths.save_json(destination / name, payload)
        checkpoint.shot_checkpoint(2)["generation_versions"] = [
            {
                "video_version": 1,
                "prompt_version": 1,
                "status": "APPROVED",
                "video_path": "shots/shot_02/v001/video.mp4",
            }
        ]
        checkpoint.save()
        (self.paths.shots_dir / "shot_01.mp4").write_bytes(b"legacy-must-not-win")
        selected = approved_shot_inputs(self.paths, checkpoint, self._board())
        self.assertEqual(
            selected[0]["path"], self.paths.shot_version_video_path(1, 1)
        )
        self.assertNotEqual(selected[0]["path"], self.paths.shots_dir / "shot_01.mp4")

    def test_G_history_ignores_legacy_versions_directory(self):
        migrate_project_to_v2(self.paths)
        (self.paths.shots_dir / "versions" / "shot_01_v999.mp4").write_bytes(
            b"legacy-extra"
        )
        history = video_version_history(
            self.paths, ProjectCheckpoint.load(self.paths), 1
        )
        self.assertEqual([item.video_version for item in history], [1, 2])
        self.assertTrue(
            all("shots/shot_01/v" in item.video_path.as_posix() for item in history)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
