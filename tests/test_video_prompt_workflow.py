from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import ProductVideoRequest, PromptGenerationError
from storyboard import CreativeBrief, Storyboard, StoryboardShot, generate_video_prompts
from storyboard_workflow import approve_storyboard_stage
from task_logger import TaskLogger
from video_prompt_workflow import (
    VideoPromptStageStateError,
    generate_video_prompts_stage,
)


def response_core(value: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"visual_prompt_core": value})}}]
    }
    return response


class VideoPromptWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.request = ProductVideoRequest(
            product_name="柠檬饮品",
            product_description="清爽饮品包装",
            user_notes="不要出现人物",
            duration_seconds=18,
            video_style="明亮商业广告",
            video_purpose="新品展示",
        )
        self.brief = CreativeBrief(
            creative_concept="清新产品旅程",
            target_audience="年轻消费者",
            key_message="自然清爽",
            visual_direction="明亮棚拍",
            narrative_arc="产品特写到品牌定格",
            global_constraints={"must": [], "must_not": ["people"]},
        )
        self.board = Storyboard(
            total_duration=18,
            shots=[
                StoryboardShot(
                    shot_id=shot_id,
                    duration=6,
                    purpose=f"purpose-{shot_id}",
                    visual=f"visual-{shot_id}",
                    camera=f"camera-{shot_id}",
                )
                for shot_id in (1, 2, 3)
            ],
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            "Video Prompt Core Test",
            self.request.model_dump(),
        )
        self.paths.save_json(self.paths.creative_brief_path(), self.brief.model_dump())
        self.paths.save_json(self.paths.storyboard_file_path(), self.board.model_dump())
        for stage, status in (
            (ProjectStage.CREATIVE, StageStatus.COMPLETED),
            (ProjectStage.CREATIVE_REVIEW, StageStatus.APPROVED),
            (ProjectStage.STORYBOARD, StageStatus.COMPLETED),
            (ProjectStage.STORYBOARD_REVIEW, StageStatus.WAITING_REVIEW),
        ):
            self.checkpoint.update_stage(stage, status)
        approve_storyboard_stage(self.checkpoint)
        self.logger = TaskLogger(self.paths, "video-prompt-core-test")

    def test_shared_stage_publishes_only_complete_plan_and_waits_for_review(self) -> None:
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_core("core-1"), response_core("core-2"), response_core("core-3")],
        ) as provider:
            plan = generate_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )

        self.assertEqual(provider.call_count, 3)
        self.assertEqual([shot.shot_id for shot in plan.shots], [1, 2, 3])
        canonical = json.loads(self.paths.video_prompts_path().read_text(encoding="utf-8"))
        self.assertEqual(len(canonical["shots"]), 3)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.VIDEO_PROMPT),
            StageStatus.COMPLETED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
            StageStatus.WAITING_REVIEW,
        )
        self.assertFalse(self.paths.shots_dir.joinpath("shot_01", "v001").exists())

    def test_failed_stage_resumes_progress_without_repeating_completed_shots(self) -> None:
        invalid = response_core("a person holds the package")
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_core("core-1"), response_core("core-2"), invalid, invalid, invalid],
        ) as first_provider:
            with self.assertRaises(PromptGenerationError) as caught:
                generate_video_prompts_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    self.logger,
                )
        self.assertEqual(first_provider.call_count, 5)
        self.checkpoint.fail(caught.exception)
        self.assertFalse(self.paths.video_prompts_path().exists())

        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_core("core-3")],
        ) as resumed_provider:
            plan = generate_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        self.assertEqual(resumed_provider.call_count, 1)
        self.assertEqual(plan.shots[0].visual_prompt_core, "core-1")
        self.assertEqual(plan.shots[1].visual_prompt_core, "core-2")
        self.assertEqual(plan.shots[2].visual_prompt_core, "core-3")

    def test_fingerprint_reuses_identical_inputs_and_invalidates_changed_board(self) -> None:
        progress = self.paths.video_prompt_generation_progress_path()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_core("one"), response_core("two"), response_core("three")],
        ):
            generate_video_prompts(
                self.request,
                self.brief,
                self.board,
                "mock-key",
                progress_path=progress,
            )
        with patch("prompt_generator.requests.post") as identical_provider:
            generate_video_prompts(
                self.request,
                self.brief,
                self.board,
                "mock-key",
                progress_path=progress,
            )
        identical_provider.assert_not_called()

        changed_payload = self.board.model_dump()
        changed_payload["shots"][0]["visual"] = "materially changed visual"
        changed_board = Storyboard.model_validate(changed_payload)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_core("new-one"), response_core("new-two"), response_core("new-three")],
        ) as changed_provider:
            generate_video_prompts(
                self.request,
                self.brief,
                changed_board,
                "mock-key",
                progress_path=progress,
            )
        self.assertEqual(changed_provider.call_count, 3)

    def test_invalid_state_and_existing_canonical_reject_before_provider(self) -> None:
        self.checkpoint.update_stage(ProjectStage.PROMPT_REVIEW, StageStatus.APPROVED)
        with (
            patch("video_prompt_workflow.generate_video_prompts") as provider,
            self.assertRaises(VideoPromptStageStateError),
        ):
            generate_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        provider.assert_not_called()

    def test_cli_calls_the_shared_stage_entry(self) -> None:
        import main

        class SharedStageReached(RuntimeError):
            pass

        with (
            patch.object(
                main,
                "generate_video_prompts_stage",
                side_effect=SharedStageReached,
            ) as shared,
            patch.object(main, "human_review_gate") as review,
        ):
            with self.assertRaises(SharedStageReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once()
        review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
