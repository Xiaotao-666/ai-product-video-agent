from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview
from shot_approval_workflow import approve_shot_stage
from shot_generation_workflow import (
    regenerate_shot_with_manual_prompt,
    resume_shot_generation,
)
from storyboard import CreativeBrief, Storyboard, VideoPromptPlan
from task_logger import TaskLogger
from tests.test_shot_generation_workflow import FakeCoreVideoGenerator
from tests.web.test_backend_phase_1b_projects import base_project, write_json, write_project
from video_generator import ProviderSubmissionUnknownError
from video_provider import VideoProviderError
from visual_input import none_visual_input


def safe_review(prompt, *_args, **_kwargs) -> PromptSafetyReview:
    return PromptSafetyReview(
        is_safe=True,
        risk_notes=[],
        reviewed_video_prompt=f"SAFE::{prompt}",
    )


class ManualPromptRegenerationCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        project = base_project(project_id="manual-core", project_name="Manual Core")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "APPROVED",
                "generation_count": 1,
                "active_prompt_version": 1,
                "approved_prompt_version": 1,
                "active_video_version": 1,
                "approved_video_version": 1,
                "pending_video_version": None,
                "prompt_version_count": 1,
                "prompt_versions": [{
                    "shot_id": 1,
                    "version": 1,
                    "source": "ai_generated",
                    "prompt": "original core\n\n[Composition Constraint]\na\n\n[Global Hard Constraints]\nb\n\n[Text Overlay Constraint]\nc\n\n[Audio Constraint]\nd",
                    "visual_prompt_core": "original core",
                    "safety_prompt": "safe original",
                    "safety_is_safe": True,
                    "safety_risk_notes": [],
                }],
                "generation_versions": [{
                    "video_version": 1,
                    "prompt_version": 1,
                    "status": "APPROVED",
                    "review_result": "APPROVED",
                    "is_active": True,
                    "is_approved": True,
                    "prompt_snapshot": {
                        "shot_id": 1,
                        "version": 1,
                        "source": "ai_generated",
                        "prompt": "original core",
                    },
                }],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        root = Path(self.temp.name)
        self.project_dir = write_project(root, "manual-core", project)
        board_payload = {
            "total_duration": 6,
            "shots": [{
                "shot_id": 1,
                "duration": 6,
                "purpose": "product focus",
                "visual": "product on table",
                "camera": "static",
                "voiceover_cues": [],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            }],
        }
        plan_payload = {"shots": [{
            "shot_id": 1,
            "visual_prompt_core": "original core",
            "video_prompt": project["video_generation"]["shots"]["1"]["prompt_versions"][0]["prompt"],
        }]}
        brief_payload = {
            "creative_concept": "focus",
            "target_audience": "adult",
            "key_message": "product",
            "visual_direction": "studio",
            "narrative_arc": "reveal",
        }
        write_json(self.project_dir / "storyboard" / "storyboard.json", board_payload)
        write_json(self.project_dir / "storyboard" / "video_prompts.json", plan_payload)
        write_json(self.project_dir / "concepts" / "creative_brief.json", brief_payload)
        self.paths = create_project_paths(self.project_dir)
        self.checkpoint = ProjectCheckpoint.load(self.paths)
        self.board = Storyboard.model_validate(board_payload)
        self.plan = VideoPromptPlan.model_validate(plan_payload)
        self.brief = CreativeBrief.model_validate(brief_payload)
        self.logger = TaskLogger(self.paths)
        version_dir = self.paths.shot_version_dir(1, 1)
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "video.mp4").write_bytes(b"official-v1")
        for name, payload in {
            "prompt.json": {"prompt_version": 1, "prompt": "original core"},
            "safety.json": {"is_safe": True},
            "generation.json": {"video_version": 1, "prompt_version": 1},
            "review.json": {"review_result": "APPROVED"},
        }.items():
            write_json(version_dir / name, payload)

    def manual(self, core: str, fake: FakeCoreVideoGenerator, *, base: int = 1):
        return regenerate_shot_with_manual_prompt(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            brief=self.brief,
            shot=self.board.shots[0],
            shot_id=1,
            base_prompt_version=base,
            edited_visual_prompt_core=core,
            visual_input=none_visual_input(),
            deepseek_key="mock",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            product_name="Manual Core",
            safety_review=safe_review,
            video_generate=fake,
        )

    def test_01_new_prompt_and_video_versions_are_independent_and_immutable(self) -> None:
        v1 = self.paths.shot_version_dir(1, 1)
        before = {name: (v1 / name).read_bytes() for name in (
            "video.mp4", "prompt.json", "safety.json", "generation.json"
        )}
        fake = FakeCoreVideoGenerator()
        output = self.manual("edited product hero core", fake)
        self.assertEqual(output, self.paths.shot_version_video_path(1, 2))
        entry = self.checkpoint.shot_checkpoint(1)
        prompt2 = self.checkpoint.prompt_version(1, 2)
        self.assertEqual(prompt2["source"], "manual_edit")
        self.assertEqual(prompt2["parent_version"], 1)
        self.assertEqual(prompt2["visual_prompt_core"], "edited product hero core")
        self.assertIn("[Audio Constraint]", prompt2["prompt"])
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 1)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["candidate"]["prompt_version"], 2)
        self.assertEqual(entry["generation_count"], 2)
        self.assertEqual(fake.submit_calls, 1)
        bundle2 = json.loads(
            (self.paths.shot_version_dir(1, 2) / "prompt.json").read_text(encoding="utf-8")
        )
        self.assertEqual(bundle2["prompt_version"], 2)
        for name, value in before.items():
            self.assertEqual((v1 / name).read_bytes(), value)

    def test_02_safety_failure_preserves_official_and_does_not_count_submit(self) -> None:
        fake = FakeCoreVideoGenerator()
        with self.assertRaises(Exception):
            regenerate_shot_with_manual_prompt(
                paths=self.paths,
                checkpoint=self.checkpoint,
                plan=self.plan,
                brief=self.brief,
                shot=self.board.shots[0],
                shot_id=1,
                base_prompt_version=1,
                edited_visual_prompt_core="unsafe edited core",
                visual_input=none_visual_input(),
                deepseek_key="mock",
                provider_credentials={"minimax": "mock"},
                task_logger=self.logger,
                safety_review=lambda *_args, **_kwargs: PromptSafetyReview(
                    is_safe=False, risk_notes=["blocked"], reviewed_video_prompt="blocked"
                ),
                video_generate=fake,
            )
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 1)
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(entry["active_prompt_version"], 2)
        self.assertEqual(fake.submit_calls, 0)

    def test_03_known_task_resume_reuses_prompt_and_video_version(self) -> None:
        fake = FakeCoreVideoGenerator(fail_after_submit=True)
        with self.assertRaises(VideoProviderError):
            self.manual("edited core for resume", fake)
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["candidate"]["prompt_version"], 2)
        self.assertEqual(entry["candidate"]["video_version"], 2)
        self.assertEqual(entry["generation_count"], 2)
        fake.fail_after_submit = False
        output = resume_shot_generation(
            paths=self.paths,
            checkpoint=self.checkpoint,
            plan=self.plan,
            shot=self.board.shots[0],
            shot_id=1,
            deepseek_key="mock",
            provider_credentials={"minimax": "mock"},
            task_logger=self.logger,
            video_generate=fake,
        )
        self.assertEqual(output, self.paths.shot_version_video_path(1, 2))
        self.assertEqual(fake.submit_calls, 1)
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["generation_count"], 2)
        self.assertIsNone(self.checkpoint.prompt_version(1, 3))
        self.assertFalse(self.paths.shot_version_dir(1, 3).exists())

    def test_04_submission_unknown_keeps_prompt_and_official_without_retry(self) -> None:
        fake = FakeCoreVideoGenerator(ambiguous=True)
        with self.assertRaises(ProviderSubmissionUnknownError):
            self.manual("edited ambiguous core", fake)
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 1)
        self.assertEqual(entry["candidate"]["prompt_version"], 2)
        self.assertEqual(entry["candidate"]["generation_phase"], "SUBMISSION_UNKNOWN")
        self.assertEqual(fake.submit_calls, 1)

    def test_05_second_manual_edit_replaces_pending_only_and_keeps_official(self) -> None:
        first = self.manual("first edited core", FakeCoreVideoGenerator())
        second = self.manual("second edited core", FakeCoreVideoGenerator(), base=2)
        self.assertEqual(first, self.paths.shot_version_video_path(1, 2))
        self.assertEqual(second, self.paths.shot_version_video_path(1, 3))
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 1)
        self.assertEqual(entry["active_prompt_version"], 3)
        self.assertEqual(entry["candidate"]["video_version"], 3)
        self.assertEqual(entry["candidate"]["prompt_version"], 3)
        self.assertEqual(entry["generation_count"], 3)
        old = next(item for item in entry["generation_versions"] if item["video_version"] == 2)
        self.assertEqual(old["review_result"], "REJECTED")
        self.assertTrue(self.paths.shot_version_dir(1, 2).is_dir())
        self.assertTrue(self.paths.shot_version_dir(1, 3).is_dir())


if __name__ == "__main__":
    unittest.main(verbosity=2)
