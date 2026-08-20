from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import Mock, patch

import tests.web.test_backend_phase_3d3c_manual_prompt_regeneration as phase3d3c
from prompt_generator import PromptSafetyReview
from shot_generation_workflow import (
    regenerate_shot_with_prompt_version,
    resume_shot_generation,
)
from tests.test_shot_generation_workflow import FakeCoreVideoGenerator
from tests.web.test_backend_phase_1b_projects import tree_snapshot, write_json
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase3D3CB2BPromptVersionGenerationTests(unittest.TestCase):
    setUp = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests.setUp
    _write_project = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._write_project
    _write_reference = staticmethod(
        phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._write_reference
    )
    wait_terminal = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests.wait_terminal
    _core = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._core
    _generate_v1 = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._generate_v1
    _generate_and_approve_v1 = (
        phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._generate_and_approve_v1
    )

    def _prepare_adopted_prompt(self) -> None:
        self._generate_and_approve_v1()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry["prompt_versions"].append(
            {
                "shot_id": 1,
                "version": 3,
                "prompt": "adopted AI revision prompt for the selected video",
                "visual_prompt_core": "adopted AI revision visual core",
                "source": "ai_revision",
                "parent_version": 2,
                "user_feedback": "make it cinematic",
                # Draft generation metadata is not a Prompt Safety decision.
                "safety_prompt": "adopted AI revision prompt for the selected video",
                "revision_metadata": {"kind": "ai_prompt_revision_draft_adoption"},
            }
        )
        entry["prompt_version_count"] = 3
        entry["active_prompt_version"] = 3
        write_json(project_path, project)

    @staticmethod
    def _payload(target: int = 3) -> dict:
        return {
            "intent": "GENERATE_WITH_PROMPT_VERSION",
            "model_selection": "AUTO",
            "requested_model": None,
            "visual_input": {"mode": "none", "asset_ids": []},
            "target_prompt_version": target,
        }

    def _preflight(self, target: int = 3):
        return self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=self._payload(target),
        )

    def _submit(
        self,
        fake: FakeCoreVideoGenerator,
        *,
        safety: Mock | None = None,
        confirm: bool = True,
    ):
        checked = self._preflight()
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertTrue(checked.json()["ready"], checked.json())
        safety_review = safety or Mock(
            return_value=PromptSafetyReview(
                is_safe=True,
                risk_notes=[],
                reviewed_video_prompt="safe adopted AI revision prompt",
            )
        )

        def shared(**kwargs):
            return regenerate_shot_with_prompt_version(
                **kwargs,
                safety_review=safety_review,
                video_generate=fake,
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_prompt_version",
            side_effect=shared,
        ) as core:
            response = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/prompt-version",
                json={
                    **self._payload(),
                    "preflight_fingerprint": checked.json()["preflight_fingerprint"],
                    "confirm_paid_call": confirm,
                },
            )
            task = (
                self.wait_terminal(response.json()["task_id"])
                if response.status_code == 202
                else None
            )
        if response.status_code == 202:
            core.assert_called_once()
        else:
            core.assert_not_called()
        return response, task, safety_review

    def _project_entry(self) -> dict:
        project = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )
        return project["video_generation"]["shots"]["1"]

    def test_01_options_and_preflight_select_adopted_prompt_without_writes(self):
        self._prepare_adopted_prompt()
        before = (self.project_dir / "project.json").read_bytes()
        options = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/options",
            params={
                "intent": "GENERATE_WITH_PROMPT_VERSION",
                "target_prompt_version": 3,
            },
        )
        self.assertEqual(options.status_code, 200, options.text)
        assert_public_payload(self, options.json())
        shot = options.json()["shot"]
        self.assertTrue(options.json()["eligible"])
        self.assertEqual(shot["official_video_version"], 1)
        self.assertEqual(shot["official_prompt_version"], 2)
        self.assertEqual(shot["prompt_version"], 3)
        self.assertEqual(shot["prompt_source"], "ai_revision")
        self.assertEqual(shot["prompt_parent_version"], 2)
        self.assertEqual(shot["next_video_version"], 2)
        checked = self._preflight().json()
        assert_public_payload(self, checked)
        self.assertTrue(checked["ready"])
        self.assertRegex(checked["preflight_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )

    def test_02_selected_generation_binds_prompt3_in_every_bundle_snapshot(self):
        self._prepare_adopted_prompt()
        before_entry = copy.deepcopy(self._project_entry())
        official = self.project_dir / "shots" / "shot_01" / "v001"
        official_snapshot = tree_snapshot(official)
        fake = FakeCoreVideoGenerator()
        response, task, safety = self._submit(fake)
        self.assertEqual(response.status_code, 202, response.text)
        assert_public_payload(self, response.json())
        self.assertEqual(response.json()["operation"], "SHOT_PROMPT_VERSION_GENERATE")
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.version, 2)
        self.assertEqual(fake.submit_calls, 1)
        safety.assert_called_once()
        self.assertEqual(
            safety.call_args.args[0],
            "adopted AI revision prompt for the selected video",
        )

        entry = self._project_entry()
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["active_prompt_version"], 3)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["candidate"]["prompt_version"], 3)
        self.assertEqual(entry["candidate"]["status"], "WAITING_REVIEW")
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(entry["prompt_versions"], before_entry["prompt_versions"])
        self.assertEqual(tree_snapshot(official), official_snapshot)

        bundle = self.project_dir / "shots" / "shot_01" / "v002"
        prompt = json.loads((bundle / "prompt.json").read_text(encoding="utf-8"))
        generation = json.loads(
            (bundle / "generation.json").read_text(encoding="utf-8")
        )
        safety_snapshot = json.loads(
            (bundle / "safety.json").read_text(encoding="utf-8")
        )
        self.assertEqual(prompt["prompt_version"], 3)
        self.assertEqual(prompt["prompt_source"], "ai_revision")
        self.assertEqual(generation["prompt_version"], 3)
        self.assertEqual(
            generation["generation_intent"], "GENERATE_WITH_PROMPT_VERSION"
        )
        self.assertEqual(
            safety_snapshot["input_prompt"],
            "adopted AI revision prompt for the selected video",
        )
        self.assertEqual(
            safety_snapshot["final_submit_prompt"],
            "safe adopted AI revision prompt",
        )
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        ).json()
        assert_public_payload(self, status)
        self.assertEqual(status["generation_intent"], "GENERATE_WITH_PROMPT_VERSION")
        self.assertEqual(status["prompt_version"], 3)

    def test_03_current_prompt_regeneration_semantics_remain_separate(self):
        self._prepare_adopted_prompt()
        current = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/options",
            params={"intent": "REGENERATE_CURRENT_PROMPT"},
        ).json()
        selected = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/options",
            params={
                "intent": "GENERATE_WITH_PROMPT_VERSION",
                "target_prompt_version": 3,
            },
        ).json()
        self.assertEqual(current["shot"]["prompt_version"], 2)
        self.assertEqual(selected["shot"]["prompt_version"], 3)

    def test_04_missing_foreign_and_non_ai_prompt_versions_are_rejected_zero_write(self):
        self._prepare_adopted_prompt()
        before = (self.project_dir / "project.json").read_bytes()
        missing = self._preflight(99).json()
        self.assertFalse(missing["ready"])
        self.assertIn(
            "PROMPT_VERSION_NOT_FOUND", {item["code"] for item in missing["issues"]}
        )

        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        foreign_entry = copy.deepcopy(entry)
        foreign_entry["prompt_versions"] = [
            {
                **copy.deepcopy(entry["prompt_versions"][-1]),
                "shot_id": 2,
                "version": 9,
            }
        ]
        foreign_entry["active_prompt_version"] = 9
        foreign_entry["prompt_version_count"] = 1
        project["video_generation"]["shots"]["2"] = foreign_entry
        write_json(project_path, project)
        foreign = self._preflight(9).json()
        self.assertFalse(foreign["ready"])
        self.assertIn(
            "PROMPT_VERSION_NOT_FOUND", {item["code"] for item in foreign["issues"]}
        )

        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry["prompt_versions"][-1]["source"] = "ai_generated"
        write_json(project_path, project)
        non_ai = self._preflight(3).json()
        self.assertFalse(non_ai["ready"])
        self.assertIn(
            "PROMPT_VERSION_NOT_ELIGIBLE",
            {item["code"] for item in non_ai["issues"]},
        )
        self.assertNotEqual(project_path.read_bytes(), before)
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )

    def test_05_prompt_hash_or_state_change_rejects_stale_paid_submit(self):
        self._prepare_adopted_prompt()
        checked = self._preflight().json()
        project_path = self.project_dir / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        entry = project["video_generation"]["shots"]["1"]
        entry["prompt_versions"][-1]["prompt"] = "changed after preflight"
        write_json(project_path, project)
        response = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/prompt-version",
            json={
                **self._payload(),
                "preflight_fingerprint": checked["preflight_fingerprint"],
                "confirm_paid_call": True,
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "GENERATION_PREFLIGHT_STALE")
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )

    def test_06_paid_confirmation_guard_creates_no_task_or_provider_call(self):
        self._prepare_adopted_prompt()
        before = (self.project_dir / "project.json").read_bytes()
        fake = FakeCoreVideoGenerator()
        response, task, safety = self._submit(fake, confirm=False)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            response.json()["error"]["code"], "PAID_CALL_CONFIRMATION_REQUIRED"
        )
        self.assertIsNone(task)
        self.assertEqual(fake.submit_calls, 0)
        safety.assert_not_called()
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)

    def test_07_safety_rejection_keeps_prompt_and_official_without_minimax_submit(self):
        self._prepare_adopted_prompt()
        before_prompts = copy.deepcopy(self._project_entry()["prompt_versions"])
        unsafe = Mock(
            return_value=PromptSafetyReview(
                is_safe=False,
                risk_notes=["unsafe"],
                reviewed_video_prompt="blocked adopted prompt",
            )
        )
        fake = FakeCoreVideoGenerator()
        _response, task, safety = self._submit(fake, safety=unsafe)
        self.assertEqual(task.status.value, "FAILED")
        self.assertEqual(task.error.code, "PROMPT_SAFETY_REJECTED")
        self.assertEqual(fake.submit_calls, 0)
        safety.assert_called_once()
        entry = self._project_entry()
        self.assertEqual(entry["prompt_versions"], before_prompts)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertFalse(
            (self.project_dir / "shots" / "shot_01" / "v002").exists()
        )

    def test_08_submission_unknown_and_provider_failure_preserve_official(self):
        self._prepare_adopted_prompt()
        _response, task, _safety = self._submit(
            FakeCoreVideoGenerator(ambiguous=True)
        )
        self.assertEqual(task.error.code, "SUBMISSION_UNKNOWN")
        entry = self._project_entry()
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["candidate"]["generation_phase"], "SUBMISSION_UNKNOWN")
        self.assertTrue(entry["candidate"]["submission_unknown"])

    def test_09_resume_reuses_target_prompt_snapshot_and_never_resubmits(self):
        self._prepare_adopted_prompt()
        first = FakeCoreVideoGenerator(fail_after_submit=True)
        _response, failed, safety = self._submit(first)
        self.assertEqual(failed.status.value, "FAILED")
        self.assertEqual(first.submit_calls, 1)
        safety.assert_called_once()
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        ).json()
        self.assertTrue(status["resume_available"])
        self.assertEqual(status["generation_intent"], "GENERATE_WITH_PROMPT_VERSION")
        self.assertEqual(status["prompt_version"], 3)

        resumed = FakeCoreVideoGenerator()

        def shared_resume(**kwargs):
            return resume_shot_generation(**kwargs, video_generate=resumed)

        with patch(
            "web_backend.services.shot_generation.resume_shot_generation",
            side_effect=shared_resume,
        ):
            response = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/resume"
            )
            task = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation"], "SHOT_RESUME")
        self.assertEqual(task.status.value, "SUCCEEDED")
        self.assertEqual(task.result.version, 2)
        self.assertEqual(resumed.submit_calls, 0)
        self.assertEqual(self._project_entry()["generation_count"], 2)
        bundle = json.loads(
            (
                self.project_dir
                / "shots"
                / "shot_01"
                / "v002"
                / "prompt.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(bundle["prompt_version"], 3)

    def test_10_approve_and_historical_restore_follow_bundle_prompt_binding(self):
        self._prepare_adopted_prompt()
        _response, task, _safety = self._submit(FakeCoreVideoGenerator())
        self.assertEqual(task.status.value, "SUCCEEDED")
        approved = self.client.post(
            "/api/projects/project-a/shots/shot_01/approve"
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        entry = self._project_entry()
        self.assertEqual(entry["approved_video_version"], 2)
        self.assertEqual(entry["approved_prompt_version"], 3)
        restored = self.client.post(
            "/api/projects/project-a/shots/shot_01/versions/1/set-official"
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        final = self._project_entry()
        self.assertEqual(final["approved_video_version"], 1)
        self.assertEqual(final["approved_prompt_version"], 2)
        self.assertEqual(final["generation_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
