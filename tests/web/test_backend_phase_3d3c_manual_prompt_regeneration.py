from __future__ import annotations

import inspect
import json
import unittest
from unittest.mock import Mock, patch

import tests.web.test_backend_phase_3d2_shot_generation as phase3d2
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview
from shot_approval_workflow import approve_shot_stage
from shot_generation_workflow import (
    generate_initial_shot,
    regenerate_shot_with_manual_prompt,
)
from shot_storage import ShotStorageError
from storyboard import CreativeBrief, Storyboard, VideoPromptPlan
from task_logger import TaskLogger
from tests.test_shot_generation_workflow import FakeCoreVideoGenerator
from tests.web.test_backend_phase_1b_projects import write_json
from video_generator import ProviderSubmissionUnknownError
from visual_input import none_visual_input
from web_backend.models.generation import GenerationIntent as WebGenerationIntent
from web_backend.services.shot_generation import _resolve_completed_generation_version


class WebBackendPhase3D3CManualPromptRegenerationTests(unittest.TestCase):
    setUp = phase3d2.WebBackendPhase3D2ShotGenerationTests.setUp
    _write_project = phase3d2.WebBackendPhase3D2ShotGenerationTests._write_project
    _write_reference = staticmethod(
        phase3d2.WebBackendPhase3D2ShotGenerationTests._write_reference
    )
    wait_terminal = phase3d2.WebBackendPhase3D2ShotGenerationTests.wait_terminal

    def _core(self):
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            {
                "creative_concept": "product focus",
                "target_audience": "adults",
                "key_message": "clear product identity",
                "visual_direction": "clean studio",
                "narrative_arc": "reveal and close",
            },
        )
        paths = create_project_paths(self.project_dir)
        checkpoint = ProjectCheckpoint.load(paths)
        board = Storyboard.model_validate_json(
            paths.storyboard_file_path().read_text(encoding="utf-8")
        )
        plan = VideoPromptPlan.model_validate_json(
            paths.video_prompts_path().read_text(encoding="utf-8")
        )
        brief = CreativeBrief.model_validate_json(
            paths.creative_brief_path().read_text(encoding="utf-8")
        )
        return paths, checkpoint, board, plan, brief

    def _generate_v1(self) -> None:
        paths, checkpoint, board, plan, _brief = self._core()
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
        paths, checkpoint, _board, _plan, _brief = self._core()
        approve_shot_stage(paths=paths, checkpoint=checkpoint, shot_id=1)

    @staticmethod
    def _payload(edited: str = "manual product hero shot with gentle camera move") -> dict:
        return {
            "intent": "REGENERATE_MANUAL_PROMPT",
            "model_selection": "AUTO",
            "requested_model": None,
            "visual_input": {"mode": "none", "asset_ids": []},
            "base_prompt_version": 2,
            "edited_prompt": edited,
        }

    def _preflight(self, payload: dict | None = None):
        self._core()
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=payload or self._payload(),
        )

    def _submit(self, fake: FakeCoreVideoGenerator, *, confirm: bool = True):
        payload = self._payload()
        checked = self._preflight(payload)
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.json()["ready"], checked.json())
        safety = Mock(
            return_value=PromptSafetyReview(
                is_safe=True,
                risk_notes=[],
                reviewed_video_prompt="safe manual prompt",
            )
        )

        def shared(**kwargs):
            return regenerate_shot_with_manual_prompt(
                **kwargs,
                safety_review=safety,
                video_generate=fake,
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_manual_prompt",
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
                core.assert_called_once()
                return response, task, safety
            core.assert_not_called()
            return response, None, safety

    def test_01_options_and_preflight_calculate_versions_without_writes(self) -> None:
        self._generate_and_approve_v1()
        before = (self.project_dir / "project.json").read_bytes()
        options = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/options",
            params={"intent": "REGENERATE_MANUAL_PROMPT"},
        )
        self.assertEqual(options.status_code, 200)
        self.assertTrue(options.json()["eligible"])
        shot = options.json()["shot"]
        self.assertEqual(shot["prompt_version"], 2)
        self.assertEqual(shot["base_video_version"], 1)
        self.assertEqual(shot["next_prompt_version"], 3)
        self.assertEqual(shot["next_video_version"], 2)
        checked = self._preflight()
        self.assertTrue(checked.json()["ready"])
        self.assertEqual(len(checked.json()["preflight_fingerprint"]), 64)
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )

    def test_02_empty_invalid_unchanged_and_stale_are_zero_write(self) -> None:
        self._generate_and_approve_v1()
        before = (self.project_dir / "project.json").read_bytes()
        empty = self._preflight(self._payload("   ")).json()
        invalid = self._preflight(
            self._payload("[Composition Constraint]\nuser override")
        ).json()
        unchanged = self._preflight(self._payload("approved active prompt")).json()
        stale_payload = self._payload()
        stale_payload["base_prompt_version"] = 1
        stale = self._preflight(stale_payload).json()
        self.assertIn("PROMPT_EMPTY", {item["code"] for item in empty["issues"]})
        self.assertIn("PROMPT_INVALID", {item["code"] for item in invalid["issues"]})
        self.assertIn("PROMPT_UNCHANGED", {item["code"] for item in unchanged["issues"]})
        self.assertIn("PROMPT_BASE_STALE", {item["code"] for item in stale["issues"]})
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )

    def test_03_paid_guard_creates_no_prompt_task_or_provider_call(self) -> None:
        self._generate_and_approve_v1()
        before = (self.project_dir / "project.json").read_bytes()
        fake = FakeCoreVideoGenerator()
        response, task, safety = self._submit(fake, confirm=False)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "PAID_CALL_CONFIRMATION_REQUIRED")
        self.assertIsNone(task)
        self.assertEqual(fake.calls, 0)
        safety.assert_not_called()
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)

    def test_04_success_creates_prompt3_video2_and_preserves_official_bundle(self) -> None:
        self._generate_and_approve_v1()
        v1 = self.project_dir / "shots" / "shot_01" / "v001"
        immutable = {name: (v1 / name).read_bytes() for name in (
            "video.mp4", "prompt.json", "safety.json", "generation.json"
        )}
        fake = FakeCoreVideoGenerator()
        response, task, safety = self._submit(fake)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "SHOT_REGENERATE")
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.version, 2)
        self.assertEqual(fake.submit_calls, 1)
        safety.assert_called_once()
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["active_prompt_version"], 3)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["candidate"]["prompt_version"], 3)
        prompt3 = next(item for item in entry["prompt_versions"] if item["version"] == 3)
        self.assertEqual(prompt3["source"], "manual_edit")
        self.assertEqual(prompt3["parent_version"], 2)
        self.assertEqual(prompt3["visual_prompt_core"], self._payload()["edited_prompt"])
        self.assertIn("[Composition Constraint]", prompt3["prompt"])
        self.assertIn("[Global Hard Constraints]", prompt3["prompt"])
        self.assertIn("[Text Overlay Constraint]", prompt3["prompt"])
        self.assertIn("[Audio Constraint]", prompt3["prompt"])
        v2 = self.project_dir / "shots" / "shot_01" / "v002"
        bundle_prompt = json.loads((v2 / "prompt.json").read_text(encoding="utf-8"))
        self.assertEqual(bundle_prompt["prompt_version"], 3)
        for name, value in immutable.items():
            self.assertEqual((v1 / name).read_bytes(), value)
        detail = self.client.get("/api/projects/project-a/shots/shot_01").json()
        self.assertEqual(detail["official_version"], 1)
        self.assertEqual(detail["pending_review_version"], 2)
        paths, checkpoint, _board, _plan, _brief = self._core()
        self.assertEqual(
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 2),
                expected_intent=WebGenerationIntent.REGENERATE_MANUAL_PROMPT,
            ),
            2,
        )
        with self.assertRaises(ShotStorageError):
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 1),
                expected_intent=WebGenerationIntent.REGENERATE_MANUAL_PROMPT,
            )

    def test_05_safety_rejection_preserves_new_prompt_but_not_video_attempt(self) -> None:
        self._generate_and_approve_v1()
        payload = self._payload()
        checked = self._preflight(payload).json()
        unsafe = Mock(
            return_value=PromptSafetyReview(
                is_safe=False,
                risk_notes=["unsafe"],
                reviewed_video_prompt="blocked prompt",
            )
        )
        fake = FakeCoreVideoGenerator()

        def shared(**kwargs):
            return regenerate_shot_with_manual_prompt(
                **kwargs, safety_review=unsafe, video_generate=fake
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_manual_prompt",
            side_effect=shared,
        ):
            accepted = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/regenerate",
                json={**payload, "preflight_fingerprint": checked["preflight_fingerprint"], "confirm_paid_call": True},
            )
            task = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(task.error.code, "PROMPT_SAFETY_REJECTED")
        self.assertEqual(fake.submit_calls, 0)
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertIsNotNone(next(item for item in entry["prompt_versions"] if item["version"] == 3))
        self.assertFalse((self.project_dir / "shots" / "shot_01" / "v002").exists())

    def test_06_approve_and_historical_restore_move_prompt_binding_without_provider(self) -> None:
        self._generate_and_approve_v1()
        response, task, _safety = self._submit(FakeCoreVideoGenerator())
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.status.value, "SUCCEEDED")
        approved = self.client.post("/api/projects/project-a/shots/shot_01/approve")
        self.assertEqual(approved.status_code, 200)
        entry = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))["video_generation"]["shots"]["1"]
        self.assertEqual(entry["approved_video_version"], 2)
        self.assertEqual(entry["approved_prompt_version"], 3)
        restored = self.client.post(
            "/api/projects/project-a/shots/shot_01/versions/1/set-official"
        )
        self.assertEqual(restored.status_code, 200)
        final = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))["video_generation"]["shots"]["1"]
        self.assertEqual(final["approved_video_version"], 1)
        self.assertEqual(final["approved_prompt_version"], 2)
        self.assertEqual(final["generation_count"], 2)

    def test_07_submission_unknown_keeps_official_and_does_not_offer_resubmit(self) -> None:
        self._generate_and_approve_v1()
        response, task, _safety = self._submit(FakeCoreVideoGenerator(ambiguous=True))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.error.code, "SUBMISSION_UNKNOWN")
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["candidate"]["generation_phase"], "SUBMISSION_UNKNOWN")
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        ).json()
        self.assertEqual(status["state"], "SUBMISSION_UNKNOWN")
        self.assertFalse(status["resume_available"])
        blocked_payload = self._payload("another manual edit")
        blocked_payload["base_prompt_version"] = 3
        blocked = self._preflight(blocked_payload).json()
        self.assertFalse(blocked["ready"])
        self.assertIn("SHOT_NOT_READY", {item["code"] for item in blocked["issues"]})
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            1,
        )

    def test_08_unapproved_active_review_manual_result_reconciles_to_video2(self) -> None:
        self.assertNotIn(
            "regenerate",
            inspect.signature(_resolve_completed_generation_version).parameters,
        )
        self._generate_v1()
        fake = FakeCoreVideoGenerator()
        response, task, safety = self._submit(fake)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.version, 2)
        self.assertEqual(fake.submit_calls, 1)
        safety.assert_called_once()

        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(entry["active_video_version"], 2)
        self.assertIsNone(entry["approved_video_version"])
        self.assertEqual(entry["active_prompt_version"], 3)
        self.assertIsNone(entry["approved_prompt_version"])
        self.assertEqual(entry["status"], "WAITING_REVIEW")
        self.assertEqual(entry["candidate"]["status"], "NONE")
        self.assertEqual(
            json.loads(
                (self.project_dir / "shots" / "shot_01" / "v002" / "prompt.json").read_text(
                    encoding="utf-8"
                )
            )["prompt_version"],
            3,
        )
        paths, checkpoint, _board, _plan, _brief = self._core()
        self.assertEqual(
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 2),
                expected_intent=WebGenerationIntent.REGENERATE_MANUAL_PROMPT,
            ),
            2,
        )

    def test_09_incomplete_completed_bundle_fails_result_reconciliation(self) -> None:
        self._generate_v1()
        payload = self._payload()
        checked = self._preflight(payload).json()
        fake = FakeCoreVideoGenerator()
        safety = Mock(
            return_value=PromptSafetyReview(
                is_safe=True,
                risk_notes=[],
                reviewed_video_prompt="safe manual prompt",
            )
        )

        def incomplete(**kwargs):
            output = regenerate_shot_with_manual_prompt(
                **kwargs,
                safety_review=safety,
                video_generate=fake,
            )
            output.with_name("safety.json").unlink()
            return output

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_manual_prompt",
            side_effect=incomplete,
        ):
            accepted = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/regenerate",
                json={
                    **payload,
                    "preflight_fingerprint": checked["preflight_fingerprint"],
                    "confirm_paid_call": True,
                },
            )
            task = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "SHOT_GENERATION_FAILED")
        self.assertEqual(fake.submit_calls, 1)
        paths, checkpoint, _board, _plan, _brief = self._core()
        with self.assertRaises(ShotStorageError):
            _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=1,
                output=paths.shot_version_video_path(1, 2),
                expected_intent=WebGenerationIntent.REGENERATE_MANUAL_PROMPT,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
