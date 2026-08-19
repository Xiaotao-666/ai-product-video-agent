from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import tests.web.test_backend_phase_3d2_shot_generation as phase3d2
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from shot_storage import ShotStorageError
from shot_approval_workflow import approve_shot_stage
from shot_generation_workflow import (
    generate_initial_shot,
    regenerate_shot_with_current_prompt,
)
from storyboard import Storyboard, VideoPromptPlan
from task_logger import TaskLogger
from tests.test_shot_generation_workflow import FakeCoreVideoGenerator
from visual_input import none_visual_input
from web_backend.models.tasks import TaskOperation, TaskRecord, TaskStatus
from web_backend.models.generation import GenerationIntent as WebGenerationIntent
from web_backend.services.shot_generation import _resolve_completed_generation_version


class WebBackendPhase3D3BShotRegenerateTests(unittest.TestCase):
    setUp = phase3d2.WebBackendPhase3D2ShotGenerationTests.setUp
    _write_project = phase3d2.WebBackendPhase3D2ShotGenerationTests._write_project
    _write_reference = staticmethod(
        phase3d2.WebBackendPhase3D2ShotGenerationTests._write_reference
    )
    payload = staticmethod(phase3d2.WebBackendPhase3D2ShotGenerationTests.payload)
    wait_terminal = phase3d2.WebBackendPhase3D2ShotGenerationTests.wait_terminal

    def _core(self):
        paths = create_project_paths(self.project_dir)
        checkpoint = ProjectCheckpoint.load(paths)
        board = Storyboard.model_validate_json(
            paths.storyboard_file_path().read_text(encoding="utf-8")
        )
        plan = VideoPromptPlan.model_validate_json(
            paths.video_prompts_path().read_text(encoding="utf-8")
        )
        return paths, checkpoint, board, plan

    def _generate_v1(self) -> None:
        paths, checkpoint, board, plan = self._core()
        generate_initial_shot(
            paths=paths,
            checkpoint=checkpoint,
            plan=plan,
            shot=board.shots[0],
            shot_id=1,
            visual_input=none_visual_input(),
            deepseek_key="",
            provider_credentials={"minimax": "mock"},
            task_logger=TaskLogger(paths),
            video_generate=FakeCoreVideoGenerator(),
        )

    def _generate_and_approve_v1(self) -> None:
        self._generate_v1()
        paths, checkpoint, _board, _plan = self._core()
        approve_shot_stage(paths=paths, checkpoint=checkpoint, shot_id=1)

    @staticmethod
    def _regenerate_payload() -> dict:
        return {
            "intent": "REGENERATE_CURRENT_PROMPT",
            "model_selection": "AUTO",
            "requested_model": None,
            "visual_input": {"mode": "none", "asset_ids": []},
        }

    def _preflight(self, payload: dict | None = None):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=payload or self._regenerate_payload(),
        )

    def _regenerate(self, fake: FakeCoreVideoGenerator, *, confirm: bool = True):
        payload = self._regenerate_payload()
        checked = self._preflight(payload)
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.json()["ready"])

        def shared(**kwargs):
            return regenerate_shot_with_current_prompt(
                **kwargs,
                video_generate=fake,
                safety_review=lambda *_args, **_kwargs: self.fail(
                    "same Prompt safety snapshot was not reused"
                ),
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_current_prompt",
            side_effect=shared,
        ) as core:
            response = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/regenerate",
                json={
                    **payload,
                    "preflight_fingerprint": checked.json()["preflight_fingerprint"],
                    "confirm_paid_call": confirm,
                },
            )
            if response.status_code == 202:
                task = self.wait_terminal(response.json()["task_id"])
                self.assertEqual(task.status.value, "SUCCEEDED")
                core.assert_called_once()
                self.last_task = task
            else:
                self.last_task = None
            return response

    def test_01_options_and_preflight_expose_current_and_next_versions(self) -> None:
        self._generate_and_approve_v1()
        options = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/options",
            params={"intent": "REGENERATE_CURRENT_PROMPT"},
        )
        self.assertEqual(options.status_code, 200)
        self.assertTrue(options.json()["eligible"])
        shot = options.json()["shot"]
        self.assertEqual(shot["prompt_version"], 2)
        self.assertEqual(shot["official_video_version"], 1)
        self.assertEqual(shot["next_video_version"], 2)
        checked = self._preflight()
        self.assertTrue(checked.json()["ready"])
        self.assertEqual(checked.json()["shot"]["next_video_version"], 2)
        self.assertEqual(len(checked.json()["preflight_fingerprint"]), 64)

    def test_02_paid_guard_creates_no_task_and_no_generation(self) -> None:
        self._generate_and_approve_v1()
        response = self._regenerate(FakeCoreVideoGenerator(), confirm=False)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"], "PAID_CALL_CONFIRMATION_REQUIRED"
        )
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            0,
        )
        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 1)

    def test_03_regenerate_creates_one_pending_bundle_and_approve_promotes_it(self) -> None:
        self._generate_and_approve_v1()
        v1 = self.project_dir / "shots" / "shot_01" / "v001"
        immutable = {
            name: (v1 / name).read_bytes()
            for name in ("video.mp4", "prompt.json", "safety.json", "generation.json")
        }
        response = self._regenerate(FakeCoreVideoGenerator())
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "SHOT_REGENERATE")
        self.assertEqual(self.last_task.result.version, 2)

        detail = self.client.get("/api/projects/project-a/shots/shot_01").json()
        self.assertEqual(detail["official_version"], 1)
        self.assertEqual(detail["pending_review_version"], 2)
        roles = {item["version"]: item["role"] for item in detail["versions"]}
        self.assertEqual(roles, {1: "OFFICIAL", 2: "PENDING_REVIEW"})

        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["candidate"]["prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(len(entry["prompt_versions"]), 1)
        v2 = self.project_dir / "shots" / "shot_01" / "v002"
        for name in ("video.mp4", "prompt.json", "safety.json", "generation.json", "review.json"):
            self.assertTrue((v2 / name).is_file(), name)
        self.assertEqual(
            json.loads((v2 / "prompt.json").read_text(encoding="utf-8"))["prompt_version"],
            2,
        )
        for name, value in immutable.items():
            self.assertEqual((v1 / name).read_bytes(), value)

        approved = self.client.post(
            "/api/projects/project-a/shots/shot_01/approve"
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["official_version"], 2)
        self.assertIsNone(approved.json()["pending_review_version"])
        self.assertEqual(
            {item["version"]: item["role"] for item in approved.json()["versions"]},
            {1: "HISTORY", 2: "OFFICIAL"},
        )
        final_entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(final_entry["approved_prompt_version"], 2)
        self.assertEqual(final_entry["generation_count"], 2)

    def test_04_stale_prompt_state_blocks_before_task_and_provider(self) -> None:
        self._generate_and_approve_v1()
        payload = self._regenerate_payload()
        checked = self._preflight(payload).json()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["video_generation"]["shots"]["1"]["prompt_versions"][0]["prompt"] = "changed"
        project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        response = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/regenerate",
            json={
                **payload,
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": True,
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "GENERATION_PREFLIGHT_STALE")
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            0,
        )

    def test_05_backend_and_frontend_task_operations_share_one_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        frontend_types = (repo_root / "frontend" / "src" / "api" / "types.ts").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"export const TASK_OPERATIONS = \[(?P<body>.*?)\] as const;",
            frontend_types,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        frontend_operations = re.findall(r'"([A-Z][A-Z0-9_]*)"', match.group("body"))
        self.assertEqual(frontend_operations, [item.value for item in TaskOperation])

    def test_06_legacy_missing_target_id_is_read_without_rewrite(self) -> None:
        repository = self.application.state.task_repository
        record = repository.create(
            TaskRecord(
                task_id="task_" + "a" * 32,
                project_id="project-a",
                operation=TaskOperation.CREATIVE_GENERATE,
                status=TaskStatus.QUEUED,
                created_at=datetime.now(timezone.utc),
                correlation_id="req_legacy_target",
            )
        )
        path = repository.tasks_root / f"{record.task_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("target_id")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        before = path.read_bytes()

        detail = self.client.get(f"/api/tasks/{record.task_id}")
        listed = self.client.get("/api/projects/project-a/tasks")

        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.json()["target_id"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["tasks"][0]["task_id"], record.task_id)
        self.assertIsNone(listed.json()["tasks"][0]["target_id"])
        self.assertEqual(path.read_bytes(), before)

    def test_07_regenerate_location_and_task_get_are_zero_side_effect(self) -> None:
        self._generate_and_approve_v1()
        response = self._regenerate(FakeCoreVideoGenerator())
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["operation"], "SHOT_REGENERATE")
        self.assertEqual(payload["target_id"], "shot_01")
        self.assertEqual(
            response.headers["Location"],
            f"/api/tasks/{payload['task_id']}",
        )
        task_path = (
            self.application.state.task_repository.tasks_root
            / f"{payload['task_id']}.json"
        )
        project_path = self.project_dir / "project.json"
        before_task = task_path.read_bytes()
        before_project = project_path.read_bytes()

        detail = self.client.get(response.headers["Location"])
        listed = self.client.get("/api/projects/project-a/tasks")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["task_id"], payload["task_id"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["tasks"][0]["target_id"], "shot_01")
        self.assertEqual(task_path.read_bytes(), before_task)
        self.assertEqual(project_path.read_bytes(), before_project)

    def test_08_unapproved_active_review_regeneration_returns_new_active_version(self) -> None:
        self._generate_v1()
        first_fake = FakeCoreVideoGenerator()
        first = self._regenerate(first_fake)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.last_task.result.version, 2)
        self.assertEqual(first_fake.submit_calls, 1)

        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertIsNone(entry["approved_video_version"])
        self.assertEqual(entry["active_video_version"], 2)
        self.assertEqual(entry["status"], "WAITING_REVIEW")
        self.assertEqual(entry["candidate"]["status"], "NONE")

        second_fake = FakeCoreVideoGenerator()
        second = self._regenerate(second_fake)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(self.last_task.result.version, 3)
        self.assertEqual(second_fake.submit_calls, 1)
        final = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(final["active_video_version"], 3)
        self.assertEqual(final["generation_count"], 3)
        self.assertEqual(final["candidate"]["status"], "NONE")
        paths, checkpoint, _board, _plan = self._core()
        self.assertEqual(
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 3),
                expected_intent=WebGenerationIntent.REGENERATE_CURRENT_PROMPT,
            ),
            3,
        )
        with self.assertRaises(ShotStorageError):
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 2),
                expected_intent=WebGenerationIntent.REGENERATE_CURRENT_PROMPT,
            )


if __name__ == "__main__":
    unittest.main()
