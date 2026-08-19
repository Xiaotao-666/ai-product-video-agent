from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview
from shot_generation_workflow import (
    InitialShotGenerationNotAllowed,
    ShotGenerationResumeUnavailable,
    generate_initial_shot,
    resume_shot_generation,
)
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


if __name__ == "__main__":
    unittest.main()
