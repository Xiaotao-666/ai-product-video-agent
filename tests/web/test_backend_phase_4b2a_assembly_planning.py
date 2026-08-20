from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import tree_snapshot
from tests.web.test_backend_phase_2d4_shots import project_payload, write_json
from tests.web.test_backend_phase_4a_multishot_foundation import shot_checkpoint
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase4B2AAssemblyPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.client = TestClient(
            create_app(settings=BackendSettings(projects_root=self.projects_root)),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def write_project(
        self,
        project_id: str,
        shot_order: list[int],
        *,
        approved_versions: dict[int, int | None],
    ) -> Path:
        project_dir = self.projects_root / project_id
        payload = project_payload(project_id)
        checkpoints: dict[str, dict] = {}
        for shot_id in reversed(shot_order):
            approved = approved_versions.get(shot_id)
            checkpoint = shot_checkpoint(
                shot_id,
                "APPROVED" if approved is not None else "NOT_STARTED",
                official=approved,
            )
            checkpoints[str(shot_id)] = checkpoint
        payload["video_generation"]["shots"] = checkpoints
        write_json(project_dir / "project.json", payload)
        write_json(
            project_dir / "storyboard" / "storyboard.json",
            {
                "total_duration": len(shot_order) * 6,
                "shots": [
                    {
                        "shot_id": shot_id,
                        "purpose": f"Shot {shot_id:02d}",
                        "duration": 6,
                    }
                    for shot_id in shot_order
                ],
            },
        )
        return project_dir

    @staticmethod
    def write_bundle(
        project_dir: Path,
        shot_id: int,
        video_version: int,
        *,
        prompt_version: int,
        duration: float = 6,
        resolution: str = "768P",
        include_all_files: bool = True,
        extra_generation: dict | None = None,
    ) -> None:
        shot_dir = project_dir / "shots" / f"shot_{shot_id:02d}"
        version_dir = shot_dir / f"v{video_version:03d}"
        write_json(
            shot_dir / "shot.json",
            {
                "shot_schema_version": 2,
                "shot_id": shot_id,
                "status": "APPROVED",
                "approved_version": video_version,
                "active_version": video_version,
                "candidate_version": None,
                "generation_count": video_version,
                "versions": list(range(1, video_version + 1)),
            },
        )
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "video.mp4").write_bytes(b"phase-4b2a-video")
        write_json(
            version_dir / "prompt.json",
            {
                "shot_id": shot_id,
                "video_version": video_version,
                "prompt_version": prompt_version,
                "prompt_text": "safe prompt",
            },
        )
        generation = {
            "shot_id": shot_id,
            "video_version": video_version,
            "prompt_version": prompt_version,
            "duration": duration,
            "resolution": resolution,
            "status": "COMPLETED",
        }
        generation.update(extra_generation or {})
        write_json(version_dir / "generation.json", generation)
        if include_all_files:
            write_json(version_dir / "safety.json", {"status": "PASS"})
            write_json(
                version_dir / "review.json",
                {
                    "shot_id": shot_id,
                    "video_version": video_version,
                    "review_result": "APPROVED",
                },
            )

    @staticmethod
    def set_approved_version(
        project_dir: Path,
        shot_id: int,
        video_version: int,
        prompt_version: int,
    ) -> None:
        project_path = project_dir / "project.json"
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        checkpoint = payload["video_generation"]["shots"][str(shot_id)]
        checkpoint.update(
            {
                "status": "APPROVED",
                "active_video_version": video_version,
                "approved_video_version": video_version,
                "active_prompt_version": prompt_version,
                "approved_prompt_version": prompt_version,
                "generation_count": video_version,
            }
        )
        checkpoint["generation_versions"].append(
            {
                "video_version": video_version,
                "prompt_version": prompt_version,
                "status": "APPROVED",
            }
        )
        write_json(project_path, payload)

    def ready_project(self, project_id: str = "assembly-ready") -> Path:
        project_dir = self.write_project(
            project_id,
            [1, 2, 3],
            approved_versions={1: 2, 2: 1, 3: 1},
        )
        self.write_bundle(project_dir, 1, 2, prompt_version=3, duration=6)
        self.write_bundle(project_dir, 2, 1, prompt_version=1, duration=8)
        self.write_bundle(project_dir, 3, 1, prompt_version=2, duration=5)
        return project_dir

    def test_01_ready_check_uses_all_approved_shots_and_backend_order(self):
        project_dir = self.write_project(
            "stable-order",
            [3, 1, 2],
            approved_versions={1: 1, 2: 1, 3: 1},
        )
        for shot_id in (1, 2, 3):
            self.write_bundle(project_dir, shot_id, 1, prompt_version=shot_id)
        response = self.client.get("/api/projects/stable-order/assembly/readiness")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["shot_count"], 3)
        self.assertEqual(payload["ready_count"], 3)
        self.assertEqual([item["shot_id"] for item in payload["shots"]], [3, 1, 2])
        self.assertEqual([item["order"] for item in payload["shots"]], [1, 2, 3])

    def test_02_plan_creation_snapshots_video_prompt_duration_and_resolution(self):
        self.ready_project()
        response = self.client.post("/api/projects/assembly-ready/assembly/plan")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["assembly_version"], 1)
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["total_duration"], 19)
        self.assertEqual(
            [
                (
                    item["shot_id"],
                    item["approved_video_version"],
                    item["prompt_version"],
                    item["duration"],
                    item["resolution"],
                )
                for item in payload["shots"]
            ],
            [(1, 2, 3, 6, "768P"), (2, 1, 1, 8, "768P"), (3, 1, 2, 5, "768P")],
        )

    def test_03_manifest_preserves_existing_assembly_history(self):
        project_dir = self.ready_project("existing-history")
        existing = {
            "manifest_version": 1,
            "assemblies": [
                {
                    "assembly_version": 1,
                    "created_at": "2026-08-01T10:00:00+08:00",
                    "final_video_path": "videos/final_video.mp4",
                    "total_duration": 19,
                    "shots": [],
                }
            ],
            "latest_assembly_version": 1,
        }
        write_json(project_dir / "videos" / "assembly_manifest.json", existing)
        response = self.client.post("/api/projects/existing-history/assembly/plan")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assembly_version"], 2)
        manifest = json.loads(
            (project_dir / "videos" / "assembly_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["assemblies"], existing["assemblies"])
        self.assertEqual(manifest["latest_assembly_version"], 1)
        self.assertEqual(manifest["latest_plan_version"], 2)

    def test_04_single_shot_project_is_compatible_and_get_is_read_only(self):
        project_dir = self.write_project(
            "single-shot",
            [1],
            approved_versions={1: 1},
        )
        self.write_bundle(project_dir, 1, 1, prompt_version=1, duration=6)
        before = tree_snapshot(project_dir)
        response = self.client.get("/api/projects/single-shot/assembly/readiness")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ready"])
        self.assertEqual(response.json()["shot_count"], 1)
        self.assertEqual(tree_snapshot(project_dir), before)

    def test_05_missing_approved_version_is_reported_and_plan_is_rejected(self):
        self.write_project(
            "missing-approved",
            [1, 2],
            approved_versions={1: None, 2: None},
        )
        readiness = self.client.get(
            "/api/projects/missing-approved/assembly/readiness"
        )
        self.assertEqual(readiness.status_code, 200, readiness.text)
        self.assertFalse(readiness.json()["ready"])
        self.assertEqual(readiness.json()["ready_count"], 0)
        self.assertEqual(
            [issue["reason"] for issue in readiness.json()["issues"]],
            ["NOT_STARTED", "NOT_STARTED"],
        )
        rejected = self.client.post("/api/projects/missing-approved/assembly/plan")
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "ASSEMBLY_NOT_READY")

    def test_06_incomplete_bundle_and_missing_video_are_distinguished(self):
        project_dir = self.write_project(
            "bundle-issues",
            [1, 2],
            approved_versions={1: 1, 2: 1},
        )
        self.write_bundle(
            project_dir,
            1,
            1,
            prompt_version=1,
            include_all_files=False,
        )
        self.write_bundle(project_dir, 2, 1, prompt_version=1)
        (project_dir / "shots" / "shot_02" / "v001" / "video.mp4").unlink()
        response = self.client.get("/api/projects/bundle-issues/assembly/readiness")
        self.assertEqual(response.status_code, 200, response.text)
        issues = {item["shot_id"]: item["reason"] for item in response.json()["issues"]}
        self.assertEqual(issues, {1: "BUNDLE_INCOMPLETE", 2: "VIDEO_MISSING"})

    def test_07_approved_version_change_marks_old_plan_outdated(self):
        project_dir = self.ready_project("outdated")
        original = self.client.post("/api/projects/outdated/assembly/plan").json()
        self.set_approved_version(project_dir, 1, 3, 4)
        self.write_bundle(project_dir, 1, 3, prompt_version=4, duration=7)
        response = self.client.get("/api/projects/outdated/assembly/readiness")
        self.assertEqual(response.status_code, 200, response.text)
        current_plan = response.json()["current_plan"]
        self.assertEqual(current_plan["status"], "OUTDATED")
        self.assertEqual(current_plan["assembly_version"], 1)
        self.assertEqual(current_plan["shots"], original["shots"])

    def test_08_new_plan_version_does_not_mutate_the_old_snapshot(self):
        project_dir = self.ready_project("new-plan")
        first = self.client.post("/api/projects/new-plan/assembly/plan").json()
        first_snapshot = deepcopy(first)
        self.set_approved_version(project_dir, 2, 2, 2)
        self.write_bundle(project_dir, 2, 2, prompt_version=2, duration=9)
        second = self.client.post("/api/projects/new-plan/assembly/plan").json()
        self.assertEqual(second["assembly_version"], 2)
        self.assertEqual(second["shots"][1]["approved_video_version"], 2)
        manifest = json.loads(
            (project_dir / "videos" / "assembly_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["plans"][0]["shots"], first_snapshot["shots"])

    def test_09_duplicate_submit_is_idempotent_and_creates_no_task(self):
        project_dir = self.ready_project("idempotent")
        first = self.client.post("/api/projects/idempotent/assembly/plan").json()
        second = self.client.post("/api/projects/idempotent/assembly/plan").json()
        self.assertEqual(second, first)
        manifest = json.loads(
            (project_dir / "videos" / "assembly_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["plans"]), 1)
        tasks = self.client.get("/api/projects/idempotent/tasks")
        self.assertEqual(tasks.status_code, 200, tasks.text)
        self.assertEqual(tasks.json()["tasks"], [])

    def test_10_dto_and_manifest_do_not_expose_provider_or_path_fields(self):
        project_dir = self.write_project(
            "safe-plan",
            [1],
            approved_versions={1: 1},
        )
        self.write_bundle(
            project_dir,
            1,
            1,
            prompt_version=1,
            extra_generation={
                "provider_task_id": "provider-secret",
                "file_id": "file-secret",
                "credential_env_name": "MINIMAX_API_KEY",
                "video_path": r"D:\private\video.mp4",
            },
        )
        response = self.client.post("/api/projects/safe-plan/assembly/plan")
        self.assertEqual(response.status_code, 200, response.text)
        assert_public_payload(self, response.json())
        manifest = json.loads(
            (project_dir / "videos" / "assembly_manifest.json").read_text(encoding="utf-8")
        )
        rendered = json.dumps(manifest["plans"], ensure_ascii=False).lower()
        for forbidden in (
            "provider_task_id",
            "provider-secret",
            "file_id",
            "file-secret",
            "credential_env_name",
            "minimax_api_key",
            "d:\\private",
            "video_path",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
