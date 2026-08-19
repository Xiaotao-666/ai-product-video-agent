from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class StoryboardWorkflowExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStage, StageStatus
        from prompt_generator import ProductVideoRequest
        from task_logger import TaskLogger
        from tests.web.test_backend_phase_3a2_creative_generate import creative_brief

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.request = ProductVideoRequest(
            product_name="测试产品",
            product_description="产品事实",
            user_notes="不要出现人物；前2秒不要出现旁白和字幕",
            duration_seconds=18,
            video_style="清爽",
            video_purpose="产品宣传",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            self.request.product_name,
            self.request.model_dump(),
        )
        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
        self.checkpoint.update_stage(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.APPROVED,
        )
        from storyboard import CreativeBrief

        brief_payload = creative_brief().model_dump()
        brief_payload["global_constraints"] = {
            "must": [],
            "must_not": ["people"],
        }
        brief_payload["av_timeline_constraints"] = {
            "forbidden_windows": [
                {
                    "start": 0,
                    "end": 2,
                    "tracks": ["voiceover", "subtitle"],
                }
            ]
        }
        self.brief = CreativeBrief.model_validate(brief_payload)
        self.paths.save_json(
            self.paths.creative_brief_path(),
            self.brief.model_dump(),
        )
        self.logger = TaskLogger(self.paths, task_id="core_storyboard_test")

    @staticmethod
    def board():
        from storyboard import Storyboard

        return Storyboard.model_validate(
            {
                "total_duration": 18,
                "shots": [
                    {
                        "shot_id": index,
                        "duration": 6,
                        "purpose": "产品展示",
                        "visual": "产品在明亮背景中展示",
                        "camera": "平稳推进",
                        "voiceover_cues": [],
                        "subtitle_cues": [],
                        "video_constraints": {
                            "reserve_subtitle_space": False,
                            "subtitle_safe_area": "none",
                        },
                    }
                    for index in range(1, 4)
                ],
            }
        )

    @staticmethod
    def planning_payload() -> dict:
        return {
            "total_duration": 18,
            "shots": [
                {
                    "shot_id": index,
                    "duration": 6,
                    "purpose": "产品展示",
                    "visual": "产品在明亮背景中展示",
                    "camera": "平稳推进",
                    "voiceover_cues": [],
                    "subtitle_cues": (
                        [{"text": "清爽一刻", "placement": "middle", "position": "bottom_center"}]
                        if index == 1
                        else []
                    ),
                    "video_constraints": {
                        "reserve_subtitle_space": index == 1,
                        "subtitle_safe_area": "bottom_center" if index == 1 else "none",
                    },
                }
                for index in range(1, 4)
            ],
        }

    def test_01_shared_callable_persists_canonical_and_waiting_review(self):
        from evaluation import EvaluationRecorder
        from project_state import ProjectStage, StageStatus
        from storyboard_workflow import generate_storyboard_stage

        with patch(
            "storyboard_workflow.generate_storyboard",
            return_value=self.board(),
        ) as provider:
            result = generate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
                evaluation_recorder=EvaluationRecorder(self.paths),
                reference_asset_context={"available": True, "asset_count": 1},
            )
        provider.assert_called_once()
        self.assertEqual(result.total_duration, 18)
        saved = json.loads(
            self.paths.storyboard_file_path().read_text(encoding="utf-8")
        )
        self.assertEqual(len(saved["shots"]), 3)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD),
            StageStatus.COMPLETED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW),
            StageStatus.WAITING_REVIEW,
        )
        evaluation = json.loads(
            self.paths.evaluation_prompt_path("storyboard").read_text(encoding="utf-8")
        )
        self.assertEqual(evaluation["records"][-1]["operation"], "generate")
        self.assertEqual(
            evaluation["records"][-1]["input_fields"]["creative_brief"][
                "creative_concept"
            ],
            self.brief.creative_concept,
        )

    def test_02_shared_callable_uses_core_scheduler_and_forbidden_window(self):
        from storyboard import schedule_av_timeline
        from storyboard_workflow import generate_storyboard_stage

        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=self.planning_payload(),
            ),
            patch(
                "storyboard.schedule_av_timeline",
                wraps=schedule_av_timeline,
            ) as scheduler,
        ):
            result = generate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        scheduler.assert_called_once()
        cue = result.shots[0].subtitle_cues[0]
        self.assertGreaterEqual(cue.start_offset, 2)
        self.assertGreater(cue.end_offset, cue.start_offset)

    def test_03_invalid_state_rejects_before_provider(self):
        from project_state import ProjectStage, StageStatus
        from storyboard_workflow import (
            StoryboardStageStateError,
            generate_storyboard_stage,
        )

        self.checkpoint.update_stage(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.WAITING_REVIEW,
        )
        with (
            patch("storyboard_workflow.generate_storyboard") as provider,
            self.assertRaises(StoryboardStageStateError),
        ):
            generate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        provider.assert_not_called()

    def test_04_failure_preserves_existing_canonical(self):
        from storyboard import StoryboardError
        from storyboard_workflow import generate_storyboard_stage

        old_payload = {"legacy": "preserve exactly"}
        self.paths.save_json(self.paths.storyboard_file_path(), old_payload)
        before = self.paths.storyboard_file_path().read_bytes()
        with (
            patch(
                "storyboard_workflow.generate_storyboard",
                side_effect=StoryboardError("SCHEDULE_UNSATISFIABLE"),
            ),
            self.assertRaises(StoryboardError),
        ):
            generate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        self.assertEqual(self.paths.storyboard_file_path().read_bytes(), before)

    def test_05_cli_calls_shared_stage_and_does_not_enter_video_prompt(self):
        import main

        class SharedStoryboardReached(RuntimeError):
            pass

        with patch.object(
            main,
            "generate_storyboard_stage",
            side_effect=SharedStoryboardReached,
        ) as shared:
            with self.assertRaises(SharedStoryboardReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once()

    def test_06_cli_resume_loads_saved_storyboard_without_regeneration(self):
        import main
        from project_state import ProjectStage, StageStatus

        self.paths.save_json(
            self.paths.storyboard_file_path(),
            self.board().model_dump(),
        )
        self.checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.COMPLETED)
        self.checkpoint.update_stage(
            ProjectStage.STORYBOARD_REVIEW,
            StageStatus.WAITING_REVIEW,
        )

        class ReviewReached(RuntimeError):
            pass

        def inspect_gate(_title, artifact, *_args, **_kwargs):
            if artifact == "storyboard":
                raise ReviewReached
            self.fail("Creative review must already be approved")

        with (
            patch.object(main, "generate_storyboard_stage") as shared,
            patch.object(main, "human_review_gate", side_effect=inspect_gate),
        ):
            with self.assertRaises(ReviewReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_not_called()

    def mark_storyboard_waiting_review(self) -> None:
        from project_state import ProjectStage, StageStatus

        self.paths.save_json(
            self.paths.storyboard_file_path(),
            self.board().model_dump(),
        )
        self.checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.COMPLETED)
        self.checkpoint.update_stage(
            ProjectStage.STORYBOARD_REVIEW,
            StageStatus.WAITING_REVIEW,
        )

    def test_07_shared_approval_transitions_to_video_prompt_without_generating(self):
        from project_state import ProjectStage, StageStatus
        from storyboard_workflow import approve_storyboard_stage

        self.mark_storyboard_waiting_review()
        approve_storyboard_stage(self.checkpoint)

        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD),
            StageStatus.COMPLETED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW),
            StageStatus.APPROVED,
        )
        self.assertEqual(self.checkpoint.next_stage(), ProjectStage.VIDEO_PROMPT)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.VIDEO_PROMPT),
            StageStatus.NOT_STARTED,
        )
        self.assertFalse(self.paths.video_prompts_path().exists())

    def test_08_shared_approval_rejects_invalid_and_repeated_calls(self):
        from storyboard_workflow import (
            StoryboardApprovalError,
            approve_storyboard_stage,
        )

        with self.assertRaises(StoryboardApprovalError):
            approve_storyboard_stage(self.checkpoint)
        self.mark_storyboard_waiting_review()
        approve_storyboard_stage(self.checkpoint)
        with self.assertRaises(StoryboardApprovalError):
            approve_storyboard_stage(self.checkpoint)

    def test_09_cli_approval_callback_uses_shared_core_callable(self):
        import main

        self.mark_storyboard_waiting_review()

        class SharedApprovalReached(RuntimeError):
            pass

        def approve_from_gate(_title, artifact, *_args, **kwargs):
            self.assertEqual(artifact, "storyboard")
            kwargs["on_approved"]()
            self.fail("shared approval should stop this gate")

        with (
            patch.object(main, "human_review_gate", side_effect=approve_from_gate),
            patch.object(
                main,
                "approve_storyboard_stage",
                side_effect=SharedApprovalReached,
            ) as shared,
        ):
            with self.assertRaises(SharedApprovalReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once_with(self.checkpoint)

    def test_10_cli_resume_after_approval_enters_video_prompt_once(self):
        import main
        from storyboard_workflow import approve_storyboard_stage

        self.mark_storyboard_waiting_review()
        approve_storyboard_stage(self.checkpoint)

        class VideoPromptReached(RuntimeError):
            pass

        with (
            patch.object(main, "generate_storyboard_stage") as storyboard_generate,
            patch.object(
                main,
                "generate_video_prompts",
                side_effect=VideoPromptReached,
            ) as video_prompt_generate,
            patch.object(main, "human_review_gate") as review_gate,
        ):
            with self.assertRaises(VideoPromptReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        storyboard_generate.assert_not_called()
        review_gate.assert_not_called()
        video_prompt_generate.assert_called_once()

    def test_11_reset_from_storyboard_clears_review_and_downstream_state(self):
        from project_state import ProjectStage, StageStatus
        from storyboard_workflow import approve_storyboard_stage

        self.mark_storyboard_waiting_review()
        approve_storyboard_stage(self.checkpoint)
        self.paths.save_json(self.paths.video_prompts_path(), {"legacy": True})
        self.checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED)

        archived = self.checkpoint.reset_from(ProjectStage.STORYBOARD)

        self.assertGreaterEqual(len(archived), 2)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD),
            StageStatus.NOT_STARTED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW),
            StageStatus.NOT_STARTED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.VIDEO_PROMPT),
            StageStatus.NOT_STARTED,
        )
        self.assertFalse(self.paths.storyboard_file_path().exists())
        self.assertFalse(self.paths.video_prompts_path().exists())

    def test_12_shared_revise_reschedules_and_returns_to_waiting_review(self):
        from evaluation import EvaluationRecorder
        from project_state import ProjectStage, StageStatus
        from storyboard import schedule_av_timeline
        from storyboard_workflow import revise_storyboard_stage

        self.mark_storyboard_waiting_review()
        revised_payload = self.planning_payload()
        revised_payload["shots"][1]["visual"] = "新增产品微距特写"
        feedback = "保留镜头数量，第二镜头增加产品微距；前2秒不要字幕。"
        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=revised_payload,
            ) as provider,
            patch(
                "storyboard.schedule_av_timeline",
                wraps=schedule_av_timeline,
            ) as scheduler,
        ):
            result = revise_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.board(),
                feedback,
                "mock-key",
                self.logger,
                approved_creative=self.brief,
                evaluation_recorder=EvaluationRecorder(self.paths),
            )

        provider.assert_called_once()
        scheduler.assert_called_once()
        self.assertIn(feedback, provider.call_args.args[2])
        self.assertEqual(result.shots[1].visual, "新增产品微距特写")
        self.assertGreaterEqual(result.shots[0].subtitle_cues[0].start_offset, 2)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD),
            StageStatus.COMPLETED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW),
            StageStatus.WAITING_REVIEW,
        )
        self.assertFalse(self.paths.video_prompts_path().exists())
        evaluation = json.loads(
            self.paths.evaluation_prompt_path("storyboard").read_text(encoding="utf-8")
        )["records"][-1]
        self.assertEqual(evaluation["operation"], "revise")
        self.assertEqual(evaluation["input_fields"]["user_feedback"], feedback)

    def test_13_shared_regenerate_is_clean_and_reschedules(self):
        from project_state import ProjectStage, StageStatus
        from storyboard import schedule_av_timeline
        from storyboard_workflow import regenerate_storyboard_stage

        old_board = self.board().model_copy(deep=True)
        old_board.shots[0].visual = "OLD_STORYBOARD_DIRECTION"
        self.paths.save_json(
            self.paths.storyboard_file_path(),
            old_board.model_dump(),
        )
        self.checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.COMPLETED)
        self.checkpoint.update_stage(
            ProjectStage.STORYBOARD_REVIEW,
            StageStatus.WAITING_REVIEW,
        )
        regenerated_payload = self.planning_payload()
        regenerated_payload["shots"][0]["visual"] = "全新产品开场"
        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=regenerated_payload,
            ) as provider,
            patch(
                "storyboard.schedule_av_timeline",
                wraps=schedule_av_timeline,
            ) as scheduler,
        ):
            result = regenerate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
                approved_creative=self.brief,
            )

        provider.assert_called_once()
        scheduler.assert_called_once()
        self.assertNotIn("OLD_STORYBOARD_DIRECTION", provider.call_args.args[2])
        self.assertEqual(result.shots[0].visual, "全新产品开场")
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW),
            StageStatus.WAITING_REVIEW,
        )
        self.assertFalse(self.paths.video_prompts_path().exists())

    def test_14_revision_failures_preserve_old_canonical_and_state(self):
        from storyboard import StoryboardError
        from storyboard_workflow import (
            regenerate_storyboard_stage,
            revise_storyboard_stage,
        )

        self.mark_storyboard_waiting_review()
        board_before = self.paths.storyboard_file_path().read_bytes()
        creative_before = self.paths.creative_brief_path().read_bytes()
        for callable_, provider_name, arguments in (
            (
                revise_storyboard_stage,
                "storyboard_workflow.revise_storyboard",
                (self.board(), "请修改第二镜头"),
            ),
            (
                regenerate_storyboard_stage,
                "storyboard_workflow.generate_storyboard",
                (),
            ),
        ):
            with (
                patch(provider_name, side_effect=StoryboardError("SCHEDULE_UNSATISFIABLE")),
                self.assertRaises(StoryboardError),
            ):
                callable_(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    *arguments,
                    "mock-key",
                    self.logger,
                    approved_creative=self.brief,
                )
            self.assertEqual(self.paths.storyboard_file_path().read_bytes(), board_before)
            self.assertEqual(self.paths.creative_brief_path().read_bytes(), creative_before)

    def test_15_storyboard_save_failure_rolls_back_feedback_constraint(self):
        from project_manager import ProjectDirectoryError
        from storyboard_workflow import revise_storyboard_stage

        self.mark_storyboard_waiting_review()
        board_before = self.paths.storyboard_file_path().read_bytes()
        creative_before = self.paths.creative_brief_path().read_bytes()
        revised_payload = self.planning_payload()
        with (
            patch(
                "storyboard.deepseek_json_request",
                return_value=revised_payload,
            ),
            patch(
                "storyboard_workflow._commit_storyboard_revision",
                side_effect=ProjectDirectoryError("simulated save failure"),
            ),
            self.assertRaises(ProjectDirectoryError),
        ):
            revise_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.board(),
                "前3秒不要字幕",
                "mock-key",
                self.logger,
                approved_creative=self.brief,
            )
        self.assertEqual(self.paths.storyboard_file_path().read_bytes(), board_before)
        self.assertEqual(self.paths.creative_brief_path().read_bytes(), creative_before)

    def test_16_invalid_review_state_rejects_both_before_provider(self):
        from storyboard_workflow import (
            StoryboardStageStateError,
            regenerate_storyboard_stage,
            revise_storyboard_stage,
        )

        with (
            patch("storyboard_workflow.revise_storyboard") as revise_provider,
            self.assertRaises(StoryboardStageStateError),
        ):
            revise_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.board(),
                "feedback",
                "mock-key",
                self.logger,
            )
        with (
            patch("storyboard_workflow.generate_storyboard") as regenerate_provider,
            self.assertRaises(StoryboardStageStateError),
        ):
            regenerate_storyboard_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        revise_provider.assert_not_called()
        regenerate_provider.assert_not_called()

    def test_17_cli_revision_callback_uses_shared_core_callable(self):
        import main

        self.mark_storyboard_waiting_review()

        class SharedRevisionReached(RuntimeError):
            pass

        def revise_from_gate(_title, artifact, _cancel, initial, _recorder, **kwargs):
            self.assertEqual(artifact, "storyboard")
            kwargs["revise"](initial, "feedback")

        with (
            patch.object(main, "human_review_gate", side_effect=revise_from_gate),
            patch.object(
                main,
                "revise_storyboard_stage",
                side_effect=SharedRevisionReached,
            ) as shared,
        ):
            with self.assertRaises(SharedRevisionReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once()

    def test_18_cli_regenerate_callback_uses_shared_core_callable(self):
        import main

        self.mark_storyboard_waiting_review()

        class SharedRegenerateReached(RuntimeError):
            pass

        def regenerate_from_gate(_title, artifact, _cancel, _initial, _recorder, **kwargs):
            self.assertEqual(artifact, "storyboard")
            kwargs["regenerate"]()

        with (
            patch.object(main, "human_review_gate", side_effect=regenerate_from_gate),
            patch.object(
                main,
                "regenerate_storyboard_stage",
                side_effect=SharedRegenerateReached,
            ) as shared,
        ):
            with self.assertRaises(SharedRegenerateReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once()


if __name__ == "__main__":
    unittest.main()
