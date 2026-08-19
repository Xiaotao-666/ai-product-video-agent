from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview
from shot_generation_workflow import (
    GenerationIntent,
    InitialShotGenerationNotAllowed,
    ShotGenerationResumeUnavailable,
    generate_initial_shot,
    regenerate_shot_with_current_prompt,
    resume_shot_generation,
)
from shot_approval_workflow import approve_shot_stage
from storyboard import Storyboard, VideoPromptPlan
from task_logger import TaskLogger
from tests.web.test_backend_phase_1b_projects import base_project, write_json, write_project
from video_generator import ProviderSubmissionUnknownError
from video_provider import ProviderTask, ProviderTaskStatus, VideoProviderError, ProviderErrorCode
from visual_input import none_visual_input


class FakeCoreVideoGenerator:
    def __init__(self, *, fail_after_submit: bool = False, ambiguous: bool = False) -> None:
        self.calls = 0
        self.submit_calls = 0
        self.events: list[str] = []
        self.fail_after_submit = fail_after_submit
        self.ambiguous = ambiguous

    def __call__(self, **kwargs):
        self.calls += 1
        task = kwargs.get("resume_task")
        kwargs["on_preflight"](
            {
                "provider": "minimax",
                "provider_model": "MiniMax-Hailuo-2.3",
                "provider_api_version": "v1",
                "generation_mode": "text_to_video",
                "selection_mode": "auto",
                "credential_env_name": "MINIMAX_API_KEY",
            }
        )
        if task is None:
            self.submit_calls += 1
            self.events.append("submitting")
            kwargs["on_submitting"](
                {
                    "provider": "minimax",
                    "provider_model": "MiniMax-Hailuo-2.3",
                    "provider_api_version": "v1",
                    "generation_mode": "text_to_video",
                    "selection_mode": "auto",
                    "credential_env_name": "MINIMAX_API_KEY",
                }
            )
            if self.ambiguous:
                raise ProviderSubmissionUnknownError(OSError("timeout"))
            task = ProviderTask(
                "minimax", "MiniMax-Hailuo-2.3", "v1", "text_to_video",
                "provider-task-core",
            )
            kwargs["on_submitted"](task)
            self.events.append("submitted")
        if self.fail_after_submit:
            raise VideoProviderError(
                ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
                "poll failed",
                retryable=True,
            )
        task = task.evolve(
            status=ProviderTaskStatus.COMPLETED,
            provider_file_id="provider-file-core",
        )
        kwargs["on_task_updated"](task)
        self.events.append("file_ready")
        kwargs["on_downloading"](task)
        output = Path(kwargs["output_path"])
        output.write_bytes(b"mock-video")
        kwargs["on_downloaded"](output)
        self.events.append("downloaded")
        return output


class ShotGenerationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        project = base_project(project_id="project-core", project_name="Core Shot")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "NOT_STARTED",
                "generation_count": 0,
                "active_prompt_version": 2,
                "prompt_versions": [
                    {
                        "shot_id": 1,
                        "version": 2,
                        "prompt": "active prompt",
                        "source": "ai_revision",
                        "safety_prompt": "safe prompt",
                        "safety_is_safe": True,
                        "safety_risk_notes": [],
                    }
                ],
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        self.project_dir = write_project(root, "project-core", project)
        board_payload = {
            "total_duration": 6,
            "shots": [{
                "shot_id": 1, "duration": 6, "purpose": "purpose",
                "visual": "visual", "camera": "static",
                "voiceover_cues": [], "subtitle_cues": [],
                "video_constraints": {"reserve_subtitle_space": False, "subtitle_safe_area": "none"},
            }],
        }
        plan_payload = {
            "shots": [{"shot_id": 1, "visual_prompt_core": "core", "video_prompt": "active prompt"}]
        }
        write_json(self.project_dir / "storyboard" / "storyboard.json", board_payload)
        write_json(self.project_dir / "storyboard" / "video_prompts.json", plan_payload)
        self.paths = create_project_paths(self.project_dir)
        self.board = Storyboard.model_validate(board_payload)
        self.plan = VideoPromptPlan.model_validate(plan_payload)
        self.checkpoint = ProjectCheckpoint.load(self.paths)
        self.logger = TaskLogger(self.paths)

    def generate(self, fake: FakeCoreVideoGenerator, *, safety_review=None):
        kwargs = {}
        if safety_review is not None:
            kwargs["safety_review"] = safety_review
        return generate_initial_shot(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            shot=self.board.shots[0],
            shot_id=1,
            visual_input=none_visual_input(),
            deepseek_key="mock-deepseek",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            video_generate=fake,
            **kwargs,
        )

    def test_01_shared_initial_callable_creates_one_unapproved_waiting_bundle(self):
        fake = FakeCoreVideoGenerator()
        output = self.generate(fake)
        self.assertTrue(output.is_file())
        self.assertEqual(output, self.paths.shot_version_video_path(1, 1))
        self.assertEqual(fake.submit_calls, 1)
        self.assertEqual(fake.events, ["submitting", "submitted", "file_ready", "downloaded"])
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["status"], "WAITING_REVIEW")
        self.assertEqual(entry["active_video_version"], 1)
        self.assertIsNone(entry["approved_video_version"])
        self.assertEqual(json.loads(self.paths.shot_version_review_path(1, 1).read_text(encoding="utf-8"))["review_result"], "WAITING_REVIEW")

    def test_02_resume_reuses_provider_task_and_generation_count(self):
        first = FakeCoreVideoGenerator(fail_after_submit=True)
        with self.assertRaises(VideoProviderError):
            self.generate(first)
        self.assertEqual(first.submit_calls, 1)
        resumed = FakeCoreVideoGenerator()
        output = resume_shot_generation(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            shot=self.board.shots[0],
            shot_id=1,
            deepseek_key="mock-deepseek",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            video_generate=resumed,
        )
        self.assertTrue(output.is_file())
        self.assertEqual(output, self.paths.shot_version_video_path(1, 1))
        self.assertEqual(resumed.submit_calls, 0)
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["generation_count"], 1)

    def test_03_ambiguous_submission_is_durable_and_not_resumable(self):
        with self.assertRaises(ProviderSubmissionUnknownError):
            self.generate(FakeCoreVideoGenerator(ambiguous=True))
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertTrue(entry["submission_unknown"])
        self.assertEqual(entry["generation_phase"], "SUBMISSION_UNKNOWN")
        with self.assertRaises(ShotGenerationResumeUnavailable):
            resume_shot_generation(
                paths=self.paths,
                checkpoint=self.checkpoint,
                plan=self.plan,
                shot=self.board.shots[0],
                shot_id=1,
                deepseek_key="mock-deepseek",
                provider_credentials={"minimax": "mock"},
                task_logger=self.logger,
                video_generate=FakeCoreVideoGenerator(),
            )

    def test_04_prompt_safety_runs_before_submit_when_no_saved_snapshot(self):
        entry = self.checkpoint.shot_checkpoint(1)
        entry["prompt_versions"][0].pop("safety_prompt", None)
        self.checkpoint.save()
        calls: list[str] = []

        def safety(prompt, key, logger, stage):
            del key, logger, stage
            calls.append(prompt)
            return PromptSafetyReview(is_safe=True, risk_notes=[], reviewed_video_prompt="reviewed")

        fake = FakeCoreVideoGenerator()
        self.generate(fake, safety_review=safety)
        self.assertEqual(calls, ["active prompt"])
        self.assertEqual(fake.submit_calls, 1)

    def test_05_initial_callable_refuses_any_second_version(self):
        self.generate(FakeCoreVideoGenerator())
        with self.assertRaises(InitialShotGenerationNotAllowed):
            self.generate(FakeCoreVideoGenerator())

    def regenerate(self, fake: FakeCoreVideoGenerator):
        return regenerate_shot_with_current_prompt(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            shot=self.board.shots[0],
            shot_id=1,
            visual_input=none_visual_input(),
            deepseek_key="",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            video_generate=fake,
            safety_review=lambda *_args, **_kwargs: self.fail(
                "saved same-Prompt safety must be reused"
            ),
        )

    def test_06_unapproved_review_regenerates_video_without_new_prompt(self):
        self.generate(FakeCoreVideoGenerator())
        output = self.regenerate(FakeCoreVideoGenerator())
        self.assertEqual(output, self.paths.shot_version_video_path(1, 2))
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["active_video_version"], 2)
        self.assertIsNone(entry["approved_video_version"])
        self.assertEqual(entry["active_prompt_version"], 2)
        self.assertEqual(len(entry["prompt_versions"]), 1)
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(entry["generation_versions"][0]["review_result"], "REJECTED")
        self.assertEqual(entry["generation_versions"][1]["prompt_version"], 2)
        self.assertEqual(
            entry["generation_versions"][1]["generation_intent"],
            GenerationIntent.REGENERATE_CURRENT_PROMPT.value,
        )

    def test_07_approved_regeneration_preserves_official_until_pending_approve(self):
        self.generate(FakeCoreVideoGenerator())
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        immutable = {
            name: (self.paths.shot_version_dir(1, 1) / name).read_bytes()
            for name in ("video.mp4", "prompt.json", "safety.json", "generation.json")
        }
        output = self.regenerate(FakeCoreVideoGenerator())
        self.assertEqual(output, self.paths.shot_version_video_path(1, 2))
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["candidate"]["status"], "WAITING_REVIEW")
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(len(entry["prompt_versions"]), 1)
        for name, value in immutable.items():
            self.assertEqual((self.paths.shot_version_dir(1, 1) / name).read_bytes(), value)
        approved = approve_shot_stage(
            paths=self.paths, checkpoint=self.checkpoint, shot_id=1
        )
        self.assertEqual(approved, 2)
        self.assertEqual(entry["approved_video_version"], 2)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["candidate"]["status"], "NONE")
        self.assertEqual(entry["generation_count"], 2)

    def test_08_regeneration_resume_reuses_same_version_and_submit(self):
        self.generate(FakeCoreVideoGenerator())
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        first = FakeCoreVideoGenerator(fail_after_submit=True)
        with self.assertRaises(VideoProviderError):
            self.regenerate(first)
        resumed = FakeCoreVideoGenerator()
        output = resume_shot_generation(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            shot=self.board.shots[0],
            shot_id=1,
            deepseek_key="",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            video_generate=resumed,
        )
        self.assertEqual(output, self.paths.shot_version_video_path(1, 2))
        self.assertEqual(resumed.submit_calls, 0)
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["candidate"]["video_version"], 2)

    def test_09_regeneration_submission_unknown_preserves_official(self):
        self.generate(FakeCoreVideoGenerator())
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        with self.assertRaises(ProviderSubmissionUnknownError):
            self.regenerate(FakeCoreVideoGenerator(ambiguous=True))
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["candidate"]["generation_phase"], "SUBMISSION_UNKNOWN")
        self.assertTrue(entry["candidate"]["submission_unknown"])
        with self.assertRaises(ShotGenerationResumeUnavailable):
            resume_shot_generation(
                paths=self.paths,
                checkpoint=self.checkpoint,
                plan=self.plan,
                shot=self.board.shots[0],
                shot_id=1,
                deepseek_key="",
                provider_credentials={"minimax": "mock"},
                task_logger=self.logger,
                video_generate=FakeCoreVideoGenerator(),
            )

    def test_10_repeated_regeneration_keeps_one_pending_and_all_bundles(self):
        self.generate(FakeCoreVideoGenerator())
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        self.regenerate(FakeCoreVideoGenerator())
        v2_video = self.paths.shot_version_video_path(1, 2).read_bytes()
        self.regenerate(FakeCoreVideoGenerator())
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["candidate"]["video_version"], 3)
        self.assertEqual(entry["candidate"]["status"], "WAITING_REVIEW")
        self.assertEqual(entry["generation_count"], 3)
        self.assertEqual(entry["generation_versions"][1]["review_result"], "REJECTED")
        self.assertEqual(self.paths.shot_version_video_path(1, 2).read_bytes(), v2_video)
        self.assertTrue(self.paths.shot_version_video_path(1, 3).is_file())

    def test_11_assembly_stales_only_after_pending_version_is_approved(self):
        self.generate(FakeCoreVideoGenerator())
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        self.checkpoint.data["assembly"] = {
            "status": "COMPLETED",
            "final_video_version": 1,
            "final_video_path": "assembly/final_v001.mp4",
            "needs_update": False,
        }
        self.checkpoint.project.save_json(self.checkpoint.path, self.checkpoint.data)
        self.regenerate(FakeCoreVideoGenerator())
        self.assertFalse(self.checkpoint.data["assembly"]["needs_update"])
        approve_shot_stage(paths=self.paths, checkpoint=self.checkpoint, shot_id=1)
        assembly = self.checkpoint.data["assembly"]
        self.assertTrue(assembly["needs_update"])
        self.assertEqual(assembly["old_approved_video_version"], 1)
        self.assertEqual(assembly["new_approved_video_version"], 2)


if __name__ == "__main__":
    unittest.main()
