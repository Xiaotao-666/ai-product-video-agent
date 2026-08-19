from __future__ import annotations

import json
import socket
import subprocess
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from shot_storage import (
    ensure_bundle_placeholders,
    sync_shot_manifest_from_checkpoint,
    write_review_snapshot,
)
from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload
from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus


class WebBackendPhase3D4AVersionManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.project_dir = self._write_versioned_project()
        self.lock_manager = ProjectLockManager()
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            ),
            lock_manager=self.lock_manager,
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    def _write_versioned_project(self) -> Path:
        project = base_project(project_id="project-a", project_name="Version roles")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT", "VIDEO_GENERATION"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "VIDEO_GENERATION"
        project["status"] = "APPROVED"
        project["video_generation"]["completed_shots"] = [1]
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "APPROVED",
                "generation_phase": "APPROVED",
                "generation_count": 3,
                "active_prompt_version": 2,
                "approved_prompt_version": 2,
                "active_video_version": 3,
                "approved_video_version": 3,
                "pending_video_version": None,
                "submission_unknown": False,
                "prompt_versions": [
                    {
                        "shot_id": 1,
                        "version": 1,
                        "prompt": "prompt bound to video one",
                        "source": "ai_generated",
                        "review_result": "APPROVED",
                    },
                    {
                        "shot_id": 1,
                        "version": 2,
                        "prompt": "prompt bound to videos two and three",
                        "source": "ai_revision",
                        "review_result": "APPROVED",
                    },
                ],
                "generation_versions": [
                    {
                        "video_version": 1,
                        "prompt_version": 1,
                        "status": "APPROVED",
                        "review_result": "APPROVED",
                        "provider_model": "MiniMax-Hailuo-2.3",
                        "is_active": False,
                        "is_approved": False,
                    },
                    {
                        "video_version": 2,
                        "prompt_version": 2,
                        "status": "REJECTED",
                        "review_result": "REJECTED",
                        "provider_model": "MiniMax-Hailuo-2.3",
                        "is_active": False,
                        "is_approved": False,
                    },
                    {
                        "video_version": 3,
                        "prompt_version": 2,
                        "status": "APPROVED",
                        "review_result": "APPROVED",
                        "provider_model": "MiniMax-Hailuo-2.3",
                        "is_active": True,
                        "is_approved": True,
                    },
                ],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        directory = write_project(self.projects_root, "project-a", project)
        write_json(
            directory / "storyboard" / "video_prompts.json",
            {
                "shots": [
                    {
                        "shot_id": 1,
                        "visual_prompt_core": "current visual core",
                        "video_prompt": "prompt bound to videos two and three",
                    }
                ]
            },
        )
        paths = create_project_paths(directory)
        for version, prompt_version, status in (
            (1, 1, "APPROVED"),
            (2, 2, "REJECTED"),
            (3, 2, "APPROVED"),
        ):
            ensure_bundle_placeholders(
                paths,
                1,
                version,
                prompt_payload={
                    "version": prompt_version,
                    "prompt": f"bundle prompt {prompt_version}",
                    "source": "ai_generated" if prompt_version == 1 else "ai_revision",
                },
                generation_payload={
                    "video_version": version,
                    "prompt_version": prompt_version,
                    "status": status,
                    "generation_phase": status,
                    "provider": "minimax",
                    "provider_model": "MiniMax-Hailuo-2.3",
                    "generation_count": version,
                },
                review_result=status,
            )
            paths.shot_version_video_path(1, version).write_bytes(
                f"immutable-video-v{version}".encode()
            )
        checkpoint = ProjectCheckpoint.load(paths)
        sync_shot_manifest_from_checkpoint(paths, 1, checkpoint.shot_checkpoint(1))
        write_review_snapshot(
            paths, 1, 1, review_result="APPROVED", user_action="approve"
        )
        write_review_snapshot(
            paths,
            1,
            2,
            review_result="REJECTED",
            user_action="regenerate_current_prompt",
        )
        write_review_snapshot(
            paths, 1, 3, review_result="APPROVED", user_action="approve"
        )
        return directory

    def detail(self) -> dict:
        response = self.client.get("/api/projects/project-a/shots/shot_01")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def set_official(self, version: str | int = 1):
        return self.client.post(
            f"/api/projects/project-a/shots/shot_01/versions/{version}/set-official",
            headers={"X-Correlation-ID": "req_set_official"},
        )

    def project(self) -> dict:
        return json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )

    def test_01_roles_and_history_reasons_are_backend_canonical(self) -> None:
        detail = self.detail()
        versions = {item["version"]: item for item in detail["versions"]}
        self.assertEqual(versions[3]["role"], "OFFICIAL")
        self.assertIsNone(versions[3]["history_reason"])
        self.assertEqual(versions[1]["role"], "HISTORY")
        self.assertEqual(versions[1]["review_status"], "APPROVED")
        self.assertEqual(versions[1]["history_reason"], "PREVIOUSLY_APPROVED")
        self.assertEqual(versions[2]["role"], "HISTORY")
        self.assertEqual(versions[2]["review_status"], "REJECTED")
        self.assertEqual(versions[2]["history_reason"], "SUPERSEDED")

    def test_02_unknown_rejection_never_claims_explicit_user_reject(self) -> None:
        review = self.project_dir / "shots" / "shot_01" / "v002" / "review.json"
        payload = json.loads(review.read_text(encoding="utf-8"))
        payload["history"] = []
        payload["user_action"] = None
        review.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(
            {item["version"]: item for item in self.detail()["versions"]}[2][
                "history_reason"
            ],
            "UNKNOWN",
        )
        write_review_snapshot(
            create_project_paths(self.project_dir),
            1,
            2,
            review_result="REJECTED",
            user_action="candidate_rejected",
        )
        self.assertEqual(
            {item["version"]: item for item in self.detail()["versions"]}[2][
                "history_reason"
            ],
            "EXPLICITLY_REJECTED",
        )

    def test_03_set_historical_approved_version_updates_all_pointers(self) -> None:
        before = self.project()["video_generation"]["shots"]["1"]
        response = self.set_official(1)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["official_version"], 1)
        self.assertIsNone(payload["pending_review_version"])
        roles = {item["version"]: item["role"] for item in payload["versions"]}
        self.assertEqual(roles, {3: "HISTORY", 2: "HISTORY", 1: "OFFICIAL"})
        after = self.project()["video_generation"]["shots"]["1"]
        self.assertEqual(after["approved_video_version"], 1)
        self.assertEqual(after["active_video_version"], 1)
        self.assertEqual(after["approved_prompt_version"], 1)
        self.assertEqual(after["active_prompt_version"], 1)
        self.assertEqual(after["generation_count"], before["generation_count"])
        self.assertEqual(after["status"], "APPROVED")
        self.assertEqual(after["candidate"]["status"], "NONE")

    def test_04_video_prompt_binding_and_bundles_are_preserved(self) -> None:
        bundle_files = {
            (version, name): (self.project_dir / "shots" / "shot_01" / f"v{version:03d}" / name).read_bytes()
            for version in (1, 2, 3)
            for name in ("video.mp4", "prompt.json", "generation.json")
        }
        self.assertEqual(self.set_official(1).status_code, 200)
        plan = json.loads(
            (self.project_dir / "storyboard" / "video_prompts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan["shots"][0]["video_prompt"], "prompt bound to video one")
        for (version, name), expected in bundle_files.items():
            self.assertEqual(
                (self.project_dir / "shots" / "shot_01" / f"v{version:03d}" / name).read_bytes(),
                expected,
                f"v{version:03d}/{name}",
            )

    def test_05_superseded_complete_version_can_be_explicitly_reapproved(self) -> None:
        original_review = json.loads(
            (self.project_dir / "shots" / "shot_01" / "v002" / "review.json").read_text(
                encoding="utf-8"
            )
        )["history"]
        response = self.set_official(2)
        self.assertEqual(response.status_code, 200)
        selected = {item["version"]: item for item in response.json()["versions"]}[2]
        self.assertEqual(selected["role"], "OFFICIAL")
        self.assertEqual(selected["review_status"], "APPROVED")
        restored = json.loads(
            (self.project_dir / "shots" / "shot_01" / "v002" / "review.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(restored["history"][: len(original_review)], original_review)
        self.assertTrue(any(item["review_result"] == "APPROVED" for item in restored["history"]))

    def test_06_switching_v3_to_v1_to_v3_is_reversible_without_generation(self) -> None:
        self.assertEqual(self.set_official(1).status_code, 200)
        self.assertEqual(self.set_official(3).status_code, 200)
        entry = self.project()["video_generation"]["shots"]["1"]
        self.assertEqual(entry["approved_video_version"], 3)
        self.assertEqual(entry["active_video_version"], 3)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["active_prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 3)
        self.assertEqual(len(self.application.state.task_repository.list_for_project("project-a")), 0)

    def test_07_pending_version_blocks_before_any_write_or_task(self) -> None:
        project = self.project()
        project["video_generation"]["shots"]["1"]["candidate"] = {
            "status": "WAITING_REVIEW",
            "video_version": 2,
            "prompt_version": 2,
        }
        (self.project_dir / "project.json").write_text(
            json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        before = (self.project_dir / "project.json").read_bytes()
        response = self.set_official(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PENDING_VERSION_REQUIRES_REVIEW")
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        self.assertEqual(len(self.application.state.task_repository.list_for_project("project-a")), 0)

    def test_08_active_task_and_lock_revalidation_block_races(self) -> None:
        repository = self.application.state.task_repository
        repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.SHOT_REGENERATE,
                target_id="shot_01",
                status=TaskStatus.RUNNING,
                created_at=datetime.now(timezone.utc),
                started_at=datetime.now(timezone.utc),
                correlation_id="req_active_shot",
            )
        )
        response = self.set_official(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")

        repository.interrupt_active_tasks()
        service = self.application.state.shot_version_service
        current_module = __import__(
            service.__class__.__module__,
            fromlist=["PendingVersionRequiresReview"],
        )
        with patch.object(
            service,
            "_require_selection_allowed",
            side_effect=[None, current_module.PendingVersionRequiresReview("race")],
        ) as revalidate:
            raced = self.set_official(1)
        self.assertEqual(raced.status_code, 409)
        self.assertEqual(raced.json()["error"]["code"], "PENDING_VERSION_REQUIRES_REVIEW")
        self.assertEqual(revalidate.call_count, 2)

    def test_09_invalid_current_incomplete_failed_and_traversal_are_rejected(self) -> None:
        self.assertEqual(self.set_official(999).status_code, 422)
        self.assertEqual(self.set_official(3).status_code, 409)
        self.assertEqual(self.set_official("v1").status_code, 422)

        prompt = self.project_dir / "shots" / "shot_01" / "v001" / "prompt.json"
        prompt.unlink()
        incomplete = self.set_official(1)
        self.assertEqual(incomplete.status_code, 409)
        self.assertEqual(incomplete.json()["error"]["code"], "ACTION_NOT_ALLOWED")

    def test_10_failed_generation_bundle_is_not_restorable(self) -> None:
        project = self.project()
        project["video_generation"]["shots"]["1"]["generation_versions"][0]["status"] = "FAILED"
        (self.project_dir / "project.json").write_text(json.dumps(project), encoding="utf-8")
        generation = self.project_dir / "shots" / "shot_01" / "v001" / "generation.json"
        payload = json.loads(generation.read_text(encoding="utf-8"))
        payload["status"] = "FAILED"
        payload["generation_phase"] = "FAILED"
        generation.write_text(json.dumps(payload), encoding="utf-8")
        response = self.set_official(1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ACTION_NOT_ALLOWED")

    def test_11_no_assembly_is_created_but_existing_assembly_becomes_stale(self) -> None:
        before = deepcopy(self.project()["assembly"])
        self.assertEqual(self.set_official(1).status_code, 200)
        self.assertEqual(self.project()["assembly"], before)

    def test_12_existing_assembly_becomes_stale_without_deleting_final(self) -> None:
        paths = create_project_paths(self.project_dir)
        final = paths.final_video_path()
        final.write_bytes(b"preserved-final")
        checkpoint = ProjectCheckpoint.load(paths)
        checkpoint.complete_assembly(
            final,
            1,
            6.0,
            [{"shot_id": 1, "approved_video_version": 3}],
        )
        self.assertEqual(self.set_official(1).status_code, 200)
        assembly = self.project()["assembly"]
        self.assertTrue(assembly["needs_update"])
        self.assertEqual(assembly["changed_shot_id"], 1)
        self.assertEqual(final.read_bytes(), b"preserved-final")

    def test_13_only_expected_local_metadata_changes_and_no_real_calls(self) -> None:
        before_dirs, before_files = tree_snapshot(self.project_dir)
        with (
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network")),
            patch.object(socket, "create_connection", side_effect=AssertionError("socket")),
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch.object(self.application.state.task_service, "submit", side_effect=AssertionError("task")),
        ):
            response = self.set_official(1)
        self.assertEqual(response.status_code, 200)
        assert_public_payload(self, response.json())
        after_dirs, after_files = tree_snapshot(self.project_dir)
        self.assertEqual(before_dirs, after_dirs)
        immutable_suffixes = {"video.mp4", "prompt.json", "generation.json", "safety.json"}
        for path, metadata in before_files.items():
            if Path(path).name in immutable_suffixes:
                self.assertEqual(after_files[path], metadata, path)
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (str(self.project_dir), "provider_task_id", "file_id", "credential"):
            self.assertNotIn(forbidden, serialized)

    def test_14_openapi_describes_a_synchronous_path_free_response(self) -> None:
        schema = self.client.get("/openapi.json").json()
        operation = schema["paths"][
            "/api/projects/{project_id}/shots/{shot_id}/versions/{video_version}/set-official"
        ]["post"]
        self.assertIn("200", operation["responses"])
        self.assertNotIn("202", operation["responses"])
        serialized = json.dumps(operation)
        for forbidden in ("provider_task_id", "file_id", "credential", "absolute_path"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
