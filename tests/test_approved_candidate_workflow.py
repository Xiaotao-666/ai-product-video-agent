from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import main
from project_manager import create_project_paths
from project_state import (
    CandidateStatus,
    ProjectCheckpoint,
    ProjectStage,
    ShotStatus,
    StageStatus,
)
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import ReviewRecorder
from shot_manager import (
    approve_candidate,
    create_candidate_prompt_version,
    edit_candidate_prompt,
    generate_candidate_video,
    manage_approved_shot,
    reject_candidate,
    save_candidate_safety,
)
from shot_review import create_prompt_version, ensure_initial_prompt_versions
from storyboard import (
    CreativeBrief,
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    VideoPromptPlan,
)
from task_logger import TaskLogger
from task_state import TaskState
from video_provider import ProviderErrorCode, VideoProviderError


def safe_review(prompt: str, *args, **kwargs) -> PromptSafetyReview:
    return PromptSafetyReview(
        is_safe=True,
        risk_notes=[],
        reviewed_video_prompt=f"SAFE::{prompt}",
    )


class CandidateMiniMax:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> Path:
        resume_task = kwargs.get("resume_task")
        resume_task_id = (
            resume_task.provider_task_id if resume_task is not None else None
        )
        resume_file_id = (
            resume_task.provider_file_id if resume_task is not None else None
        )
        self.calls.append(
            {
                "shot_id": int(kwargs["shot_id"]),
                "prompt": kwargs["prompt"],
                "resume_task_id": resume_task_id,
                "resume_file_id": resume_file_id,
                "output_path": kwargs["output_path"],
            }
        )
        task_id = resume_task_id
        if not task_id:
            task_id = f"candidate-task-{kwargs['shot_id']}-{len(self.calls)}"
            kwargs["on_submitted"](task_id)
        if self.fail:
            raise VideoProviderError(
                ProviderErrorCode.TASK_FAILED, "mock candidate failure"
            )
        file_id = resume_file_id
        if not file_id:
            file_id = f"candidate-file-{kwargs['shot_id']}-{len(self.calls)}"
            kwargs["on_task_updated"](file_id)
        output = kwargs["output_path"]
        output.write_bytes(
            f"candidate-shot-{kwargs['shot_id']}-call-{len(self.calls)}".encode()
        )
        return output


class ApprovedCandidateWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "candidate-project")
        self.request = ProductVideoRequest(
            product_name="候选项目",
            product_description="产品信息",
            duration_seconds=18,
            video_style="统一自然光",
            video_purpose="品牌宣传",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "候选项目", self.request.model_dump()
        )
        self.brief = CreativeBrief(
            creative_concept="概念",
            target_audience="受众",
            key_message="信息",
            visual_direction="统一自然光",
            narrative_arc="三镜头结构",
        )
        self.board = Storyboard(
            total_duration=18,
            shots=[
                StoryboardShot(
                    shot_id=shot_id,
                    duration=6,
                    purpose=f"目的{shot_id}",
                    visual=f"画面{shot_id}",
                    camera=f"运镜{shot_id}",
                )
                for shot_id in (1, 2, 3)
            ],
        )
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(shot_id=shot_id, video_prompt=f"prompt-{shot_id}-v1")
                for shot_id in (1, 2, 3)
            ]
        )
        self.paths.save_json(self.paths.creative_brief_path(), self.brief.model_dump())
        self.paths.save_json(self.paths.storyboard_file_path(), self.board.model_dump())
        self.paths.save_json(self.paths.video_prompts_path(), self.plan.model_dump())
        for stage, status in (
            (ProjectStage.CREATIVE, StageStatus.COMPLETED),
            (ProjectStage.CREATIVE_REVIEW, StageStatus.APPROVED),
            (ProjectStage.STORYBOARD, StageStatus.COMPLETED),
            (ProjectStage.STORYBOARD_REVIEW, StageStatus.APPROVED),
            (ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED),
            (ProjectStage.PROMPT_REVIEW, StageStatus.APPROVED),
            (ProjectStage.VIDEO_GENERATION, StageStatus.COMPLETED),
        ):
            self.checkpoint.update_stage(stage, status)
        self.checkpoint.ensure_shots([1, 2, 3])
        self.logger = TaskLogger(self.paths, "candidate-test")
        ensure_initial_prompt_versions(
            self.paths, self.checkpoint, self.plan, self.logger
        )
        for shot_id in (1, 2, 3):
            create_prompt_version(
                self.paths,
                self.checkpoint,
                self.plan,
                shot_id,
                f"prompt-{shot_id}-v2",
                "manual_edit",
                self.logger,
                parent_version=1,
                original_prompt=f"prompt-{shot_id}-v1",
            )
            create_prompt_version(
                self.paths,
                self.checkpoint,
                self.plan,
                shot_id,
                f"approved-prompt-{shot_id}-v3",
                "manual_edit",
                self.logger,
                parent_version=2,
                original_prompt=f"prompt-{shot_id}-v2",
            )
            active = self.paths.shot_version_video_path(shot_id, 3)
            active.parent.mkdir(parents=True, exist_ok=True)
            active.write_bytes(f"approved-shot-{shot_id}-v3".encode())
            entry = self.checkpoint.shot_checkpoint(shot_id)
            entry.update(
                {
                    "status": ShotStatus.APPROVED.value,
                    "generation_count": 3,
                    "active_prompt_version": 3,
                    "active_video_version": 3,
                    "approved_prompt_version": 3,
                    "approved_video_version": 3,
                    "provider_task_id": f"approved-task-{shot_id}",
                    "file_id": f"approved-file-{shot_id}",
                    "generation_versions": [
                        {
                            "video_version": 3,
                            "prompt_version": 3,
                            "status": ShotStatus.APPROVED.value,
                            "provider_task_id": f"approved-task-{shot_id}",
                            "file_id": f"approved-file-{shot_id}",
                        }
                    ],
                }
            )
        self.checkpoint.data["video_generation"]["completed_shots"] = [1, 2, 3]
        self.checkpoint.save()
        self.paths.save_json(self.paths.video_prompts_path(), self.plan.model_dump())
        self.recorder = ReviewRecorder(
            self.paths,
            self.request.model_dump(),
            "candidate-review",
            self.logger,
            initial_state=TaskState.APPROVED,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_same_candidate(self, shot_id: int = 2) -> None:
        approved = self.checkpoint.prompt_version(shot_id, 3)
        self.checkpoint.begin_candidate_editing(shot_id, None)
        create_candidate_prompt_version(
            self.paths,
            self.checkpoint,
            shot_id,
            approved["prompt"],
            "same_prompt",
            self.logger,
            parent_version=3,
        )
        self.checkpoint.prepare_candidate_generation(shot_id)

    def generate_candidate(self, shot_id: int = 2, generator=None) -> CandidateMiniMax:
        minimax = generator or CandidateMiniMax()
        generate_candidate_video(
            self.paths,
            self.checkpoint,
            self.request,
            self.board.shots[shot_id - 1],
            shot_id,
            "deepseek-mock",
            "minimax-mock",
            self.logger,
            safety_review=safe_review,
            video_generate=minimax,
        )
        return minimax

    def assert_approved_v3(self, shot_id: int = 2) -> None:
        entry = self.checkpoint.shot_checkpoint(shot_id)
        self.assertEqual(entry["status"], ShotStatus.APPROVED.value)
        self.assertEqual(entry["approved_prompt_version"], 3)
        self.assertEqual(entry["approved_video_version"], 3)
        self.assertIn(
            f"approved-shot-{shot_id}-v3".encode(),
            self.paths.shot_version_video_path(shot_id, 3).read_bytes(),
        )

    def test_A_approved_v3_creates_candidate_v4_without_replacing_approved(self):
        self.create_same_candidate()
        self.generate_candidate()
        self.assert_approved_v3()
        candidate = self.checkpoint.candidate_checkpoint(2)
        self.assertEqual(candidate["status"], CandidateStatus.WAITING_REVIEW.value)
        self.assertEqual(candidate["prompt_version"], 4)
        self.assertEqual(candidate["video_version"], 4)
        self.assertTrue(self.paths.shot_version_video_path(2, 4).is_file())
        approved_payload = self.checkpoint.prompt_version(2, 3)
        candidate_payload = self.checkpoint.prompt_version(2, 4)
        self.assertNotEqual(approved_payload.get("safety_prompt"), "SAFE::approved-prompt-2-v3")
        self.assertEqual(candidate_payload["safety_prompt"], "SAFE::approved-prompt-2-v3")

    def test_B_rejected_candidate_preserves_approved_v3_and_archives_candidate(self):
        self.create_same_candidate()
        self.generate_candidate()
        reject_candidate(
            self.paths, self.checkpoint, self.recorder, self.logger, 2
        )
        self.assert_approved_v3()
        self.assertEqual(self.checkpoint.candidate_status(2), CandidateStatus.NONE)
        self.assertTrue(self.paths.shot_version_video_path(2, 4).is_file())
        payload = self.checkpoint.prompt_version(2, 4)
        self.assertEqual(payload["review_result"], "REJECTED")

    def test_C_approved_candidate_becomes_v4_and_preserves_v3_history(self):
        self.create_same_candidate()
        self.generate_candidate()
        approve_candidate(
            self.paths, self.checkpoint, self.plan, self.recorder, self.logger, 2
        )
        entry = self.checkpoint.shot_checkpoint(2)
        self.assertEqual(entry["status"], ShotStatus.APPROVED.value)
        self.assertEqual(entry["approved_prompt_version"], 4)
        self.assertEqual(entry["approved_video_version"], 4)
        self.assertEqual(self.checkpoint.candidate_status(2), CandidateStatus.NONE)
        self.assertTrue(self.paths.shot_version_video_path(2, 3).is_file())
        self.assertIn(b"candidate-shot-2", self.paths.shot_version_video_path(2, 4).read_bytes())
        self.assertIsNotNone(self.checkpoint.prompt_version(2, 3))

    def test_D_ai_revision_changes_only_selected_shot(self):
        before_1 = deepcopy(self.checkpoint.prompt_version(1, 3))
        before_3 = deepcopy(self.checkpoint.prompt_version(3, 3))
        reviser = Mock(return_value="AI candidate for shot 2")
        minimax = CandidateMiniMax()
        with patch("builtins.input", side_effect=["4", "更慢", "1", "7"]):
            manage_approved_shot(
                self.paths,
                self.checkpoint,
                self.request,
                self.plan,
                self.board.shots[1],
                self.recorder,
                self.logger,
                "deepseek-mock",
                "minimax-mock",
                revise=reviser,
                safety_review=safe_review,
                video_generate=minimax,
            )
        reviser.assert_called_once_with("approved-prompt-2-v3", "更慢")
        self.assertEqual(self.checkpoint.prompt_version(1, 3), before_1)
        self.assertEqual(self.checkpoint.prompt_version(3, 3), before_3)
        self.assertEqual([call["shot_id"] for call in minimax.calls], [2])
        self.assert_approved_v3()

    def test_E_manual_edit_uses_prefilled_approved_prompt_without_llm(self):
        seen: list[str] = []

        def editor(path: Path) -> None:
            seen.append(path.read_text(encoding="utf-8"))
            path.write_text("manual candidate shot 2", encoding="utf-8")

        reviser = Mock(side_effect=AssertionError("manual Candidate must not call LLM"))
        minimax = CandidateMiniMax()
        with patch("builtins.input", side_effect=["5", "1", "7"]):
            manage_approved_shot(
                self.paths,
                self.checkpoint,
                self.request,
                self.plan,
                self.board.shots[1],
                self.recorder,
                self.logger,
                "deepseek-mock",
                "minimax-mock",
                revise=reviser,
                safety_review=safe_review,
                video_generate=minimax,
                editor=editor,
            )
        self.assertEqual(seen, ["approved-prompt-2-v3"])
        payload = self.checkpoint.prompt_version(2, 4)
        self.assertEqual(payload["source"], "manual_edit")
        self.assertEqual(payload["original_prompt"], "approved-prompt-2-v3")
        self.assertEqual(payload["safety_prompt"], "SAFE::manual candidate shot 2")
        reviser.assert_not_called()

    def test_F_candidate_failure_does_not_change_approved(self):
        self.create_same_candidate()
        self.generate_candidate(generator=CandidateMiniMax(fail=True))
        self.assert_approved_v3()
        self.assertEqual(self.checkpoint.candidate_status(2), CandidateStatus.FAILED)
        self.assertIn(
            "mock candidate failure",
            self.checkpoint.candidate_checkpoint(2)["last_error"]["message"],
        )

    def test_G_generating_resume_reuses_provider_task_id(self):
        self.create_same_candidate()
        payload = self.checkpoint.prompt_version(2, 4)
        save_candidate_safety(
            self.paths,
            self.checkpoint,
            2,
            safe_review(payload["prompt"]),
        )
        self.checkpoint.mark_candidate_submitted(2, "existing-candidate-task")
        minimax = CandidateMiniMax()
        with patch("builtins.input", side_effect=["7"]):
            manage_approved_shot(
                self.paths,
                self.checkpoint,
                self.request,
                self.plan,
                self.board.shots[1],
                self.recorder,
                self.logger,
                "deepseek-mock",
                "minimax-mock",
                revise=Mock(),
                safety_review=Mock(side_effect=AssertionError("saved safety must be reused")),
                video_generate=minimax,
            )
        self.assertEqual(len(minimax.calls), 1)
        self.assertEqual(minimax.calls[0]["resume_task_id"], "existing-candidate-task")
        self.assertEqual(self.checkpoint.candidate_status(2), CandidateStatus.WAITING_REVIEW)

    def test_H_waiting_review_resume_does_not_regenerate(self):
        self.create_same_candidate()
        self.generate_candidate()
        forbidden = Mock(side_effect=AssertionError("WAITING_REVIEW must not regenerate"))
        with patch("builtins.input", side_effect=["7"]):
            manage_approved_shot(
                self.paths,
                self.checkpoint,
                self.request,
                self.plan,
                self.board.shots[1],
                self.recorder,
                self.logger,
                "deepseek-mock",
                "minimax-mock",
                revise=Mock(),
                safety_review=Mock(),
                video_generate=forbidden,
            )
        forbidden.assert_not_called()
        self.assertEqual(self.checkpoint.candidate_status(2), CandidateStatus.WAITING_REVIEW)

    def test_I_approving_shot_two_does_not_change_shots_one_or_three(self):
        before_1 = deepcopy(self.checkpoint.shot_checkpoint(1))
        before_3 = deepcopy(self.checkpoint.shot_checkpoint(3))
        bytes_1 = self.paths.shot_version_video_path(1, 3).read_bytes()
        bytes_3 = self.paths.shot_version_video_path(3, 3).read_bytes()
        self.create_same_candidate()
        self.generate_candidate()
        approve_candidate(
            self.paths, self.checkpoint, self.plan, self.recorder, self.logger, 2
        )
        self.assertEqual(self.checkpoint.shot_checkpoint(1), before_1)
        self.assertEqual(self.checkpoint.shot_checkpoint(3), before_3)
        self.assertEqual(self.paths.shot_version_video_path(1, 3).read_bytes(), bytes_1)
        self.assertEqual(self.paths.shot_version_video_path(3, 3).read_bytes(), bytes_3)

    def test_J_existing_final_video_is_kept_and_assembly_marked_stale(self):
        final = self.paths.videos_dir / "final_video.mp4"
        final.write_bytes(b"old-final")
        self.create_same_candidate()
        self.generate_candidate()
        approve_candidate(
            self.paths, self.checkpoint, self.plan, self.recorder, self.logger, 2
        )
        self.assertEqual(final.read_bytes(), b"old-final")
        assembly = self.checkpoint.data["assembly"]
        self.assertTrue(assembly["needs_update"])
        self.assertEqual(assembly["changed_shot_id"], 2)
        self.assertEqual(assembly["old_approved_video_version"], 3)
        self.assertEqual(assembly["new_approved_video_version"], 4)

    def test_K_normal_resume_skips_all_approved_shots_and_candidate_manager(self):
        self.checkpoint.data["stages"][ProjectStage.COMPLETED.value]["status"] = (
            StageStatus.NOT_STARTED.value
        )
        self.checkpoint.data["stages"][ProjectStage.VIDEO_GENERATION.value]["status"] = (
            StageStatus.RUNNING.value
        )
        self.checkpoint.data["status"] = StageStatus.RUNNING.value
        self.checkpoint.data["current_stage"] = ProjectStage.VIDEO_GENERATION.value
        self.checkpoint.save()
        forbidden_video = Mock(side_effect=AssertionError("APPROVED Shots must be skipped"))
        with (
            patch.object(main, "generate_video", forbidden_video),
            patch.object(main, "shot_management_menu", Mock(side_effect=AssertionError("normal Resume must not enter management"))),
        ):
            main.run_pipeline(
                self.paths,
                self.request,
                self.checkpoint,
                "deepseek-mock",
                "minimax-mock",
                TaskLogger(self.paths, "normal-resume"),
            )
        forbidden_video.assert_not_called()
        self.assertTrue(self.checkpoint.all_shots_approved([1, 2, 3]))
        self.assertEqual(self.checkpoint.status, StageStatus.COMPLETED.value)

    def test_resume_unconfirmed_candidate_edit_reopens_temp_copy_not_active_prompt(self):
        editing = self.paths.shot_prompt_edit_path(2, "candidate-interrupted")
        editing.write_text("unconfirmed candidate edit", encoding="utf-8")
        self.checkpoint.begin_candidate_editing(2, editing)
        self.checkpoint.candidate_checkpoint(2)["editing_original_prompt"] = (
            "approved-prompt-2-v3"
        )
        self.checkpoint.save()
        seen: list[str] = []

        def editor(path: Path) -> None:
            seen.append(path.read_text(encoding="utf-8"))
            path.write_text("resumed confirmed edit", encoding="utf-8")

        with patch("builtins.input", side_effect=["1"]):
            edited = edit_candidate_prompt(
                self.paths,
                self.checkpoint,
                2,
                self.logger,
                "approved-prompt-2-v3",
                editor=editor,
                resume_existing=True,
                editing_path=editing,
            )
        self.assertEqual(seen, ["unconfirmed candidate edit"])
        self.assertEqual(edited, "resumed confirmed edit")
        self.assertEqual(
            self.checkpoint.shot_checkpoint(2)["active_prompt_version"], 3
        )
        self.assertEqual(
            self.checkpoint.shot_checkpoint(2)["approved_prompt_version"], 3
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
