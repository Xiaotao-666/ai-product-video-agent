from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from main import run_pipeline
from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import (
    JSONDuplicateKeyError,
    MAX_JSON_REQUEST_ATTEMPTS,
    ProductVideoRequest,
    PromptGenerationError,
    strict_json_loads,
)
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardShot,
    VideoPromptStructureError,
    _validate_video_prompt_payload,
    generate_video_prompts,
)
from task_logger import TaskLogger
from video_prompt_recovery import (
    recover_project_video_prompts,
    recover_video_prompt_plan_from_raw,
)


def response_with(content: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def board() -> Storyboard:
    return Storyboard(
        total_duration=12,
        shots=[
            StoryboardShot(
                shot_id=shot_id,
                duration=6,
                purpose=f"purpose-{shot_id}",
                visual=f"visual-{shot_id}",
                camera=f"camera-{shot_id}",
            )
            for shot_id in (1, 2)
        ],
    )


def request() -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="P",
        product_description="D",
        duration_seconds=12,
        video_style="S",
        video_purpose="U",
    )


def brief() -> CreativeBrief:
    return CreativeBrief(
        creative_concept="concept",
        target_audience="audience",
        key_message="message",
        visual_direction="visual",
        narrative_arc="arc",
    )


VALID_PROMPTS = json.dumps(
    {
        "shots": [
            {"shot_id": 1, "video_prompt": "prompt-1"},
            {"shot_id": 2, "video_prompt": "prompt-2"},
        ]
    },
    ensure_ascii=False,
)
MALFORMED_DUPLICATE = (
    '{"shots":[{"shot_id":1,"video_prompt":"prompt-1",'
    '"shot_id":2,"video_prompt":"prompt-2"}]}'
)
MALFORMED_CORE_DUPLICATE = (
    '{"visual_prompt_core":"prompt-1","visual_prompt_core":"prompt-2"}'
)
VALID_CORE_1 = json.dumps({"visual_prompt_core": "prompt-1"}, ensure_ascii=False)
VALID_CORE_2 = json.dumps({"visual_prompt_core": "prompt-2"}, ensure_ascii=False)


class StructuredOutputTests(unittest.TestCase):
    def test_A_standard_prompt_json_parses(self) -> None:
        parsed = strict_json_loads(VALID_PROMPTS)
        _validate_video_prompt_payload(parsed, board())
        self.assertEqual([shot["shot_id"] for shot in parsed["shots"]], [1, 2])

    def test_B_duplicate_shot_keys_are_rejected(self) -> None:
        with self.assertRaises(JSONDuplicateKeyError):
            strict_json_loads(MALFORMED_DUPLICATE)

    def test_C_duplicate_generic_keys_are_rejected(self) -> None:
        with self.assertRaises(JSONDuplicateKeyError) as caught:
            strict_json_loads('{"foo":1,"nested":{"foo":2,"foo":3}}')
        self.assertEqual(caught.exception.duplicate_key, "foo")

    def test_D_missing_shot_is_rejected(self) -> None:
        with self.assertRaises(VideoPromptStructureError):
            _validate_video_prompt_payload(
                {"shots": [{"shot_id": 1, "video_prompt": "p"}]}, board()
            )

    def test_E_duplicate_shot_id_is_rejected(self) -> None:
        with self.assertRaises(VideoPromptStructureError):
            _validate_video_prompt_payload(
                {
                    "shots": [
                        {"shot_id": 1, "video_prompt": "p1"},
                        {"shot_id": 1, "video_prompt": "p2"},
                    ]
                },
                board(),
            )

    def test_F_out_of_order_shots_are_rejected(self) -> None:
        with self.assertRaises(VideoPromptStructureError):
            _validate_video_prompt_payload(
                {
                    "shots": [
                        {"shot_id": 2, "video_prompt": "p2"},
                        {"shot_id": 1, "video_prompt": "p1"},
                    ]
                },
                board(),
            )

    def test_G_invalid_then_valid_response_retries_and_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = create_project_paths(Path(temp) / "project")
            logger = TaskLogger(paths, "retry-success")
            with patch(
                "prompt_generator.requests.post",
                side_effect=[
                    response_with(MALFORMED_CORE_DUPLICATE),
                    response_with(VALID_CORE_1),
                    response_with(VALID_CORE_2),
                ],
            ) as post:
                plan = generate_video_prompts(request(), brief(), board(), "mock-key", logger)
            self.assertEqual([shot.shot_id for shot in plan.shots], [1, 2])
            self.assertEqual(post.call_count, 3)
            task_log = logger.task_log_path.read_text(encoding="utf-8")
            self.assertIn("STRUCTURED_JSON_DUPLICATE_KEY", task_log)
            self.assertIn("VIDEO_PROMPT_RETRY", task_log)

    def test_H_all_invalid_responses_end_in_failed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = create_project_paths(Path(temp) / "project")
            logger = TaskLogger(paths, "retry-failed")
            checkpoint = ProjectCheckpoint.create(paths, "P", request().model_dump())
            checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.RUNNING)
            with patch(
                "prompt_generator.requests.post",
                side_effect=[
                    response_with(MALFORMED_CORE_DUPLICATE)
                    for _ in range(MAX_JSON_REQUEST_ATTEMPTS)
                ],
            ) as post:
                try:
                    generate_video_prompts(request(), brief(), board(), "mock-key", logger)
                except PromptGenerationError as exc:
                    checkpoint.fail(exc)
                else:
                    self.fail("Expected PromptGenerationError")
            self.assertEqual(post.call_count, MAX_JSON_REQUEST_ATTEMPTS)
            self.assertEqual(
                checkpoint.stage_status(ProjectStage.VIDEO_PROMPT), StageStatus.FAILED
            )

    def test_I_known_raw_duplicate_pairs_recover_exact_ids(self) -> None:
        plan = recover_video_prompt_plan_from_raw(MALFORMED_DUPLICATE, board())
        self.assertEqual([shot.shot_id for shot in plan.shots], [1, 2])
        self.assertEqual([shot.video_prompt for shot in plan.shots], ["prompt-1", "prompt-2"])

    def test_J_recovered_project_resumes_at_prompt_review_without_api(self) -> None:
        class PromptReviewReached(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temp:
            paths = create_project_paths(Path(temp) / "project")
            req = request()
            checkpoint = ProjectCheckpoint.create(paths, "P", req.model_dump())
            paths.save_json(paths.creative_brief_path(), brief().model_dump())
            paths.save_json(paths.storyboard_file_path(), board().model_dump())
            for stage, status in (
                (ProjectStage.CREATIVE, StageStatus.COMPLETED),
                (ProjectStage.CREATIVE_REVIEW, StageStatus.APPROVED),
                (ProjectStage.STORYBOARD, StageStatus.COMPLETED),
                (ProjectStage.STORYBOARD_REVIEW, StageStatus.APPROVED),
                (ProjectStage.VIDEO_PROMPT, StageStatus.RUNNING),
            ):
                checkpoint.update_stage(stage, status)
            checkpoint.fail("mock malformed response")
            raw_path = paths.llm_raw_file_path("video_prompt", "recover")
            raw_path.write_text(MALFORMED_DUPLICATE, encoding="utf-8")
            recovery_logger = TaskLogger(paths, "recover")

            with patch("prompt_generator.requests.post") as post:
                recover_project_video_prompts(paths, raw_path, recovery_logger)
            post.assert_not_called()

            resumed = ProjectCheckpoint.load(paths)
            self.assertEqual(
                resumed.stage_status(ProjectStage.VIDEO_PROMPT), StageStatus.COMPLETED
            )
            self.assertEqual(
                resumed.stage_status(ProjectStage.PROMPT_REVIEW),
                StageStatus.WAITING_REVIEW,
            )
            self.assertEqual(resumed.current_stage, ProjectStage.PROMPT_REVIEW)

            with (
                patch("main.generate_video_prompts") as generate,
                patch("main.human_review_gate", side_effect=PromptReviewReached) as gate,
            ):
                with self.assertRaises(PromptReviewReached):
                    run_pipeline(
                        paths,
                        req,
                        resumed,
                        "mock-deepseek-key",
                        "mock-minimax-key",
                        TaskLogger(paths, "resume"),
                    )
            generate.assert_not_called()
            self.assertEqual(gate.call_args.args[0], "Video Prompt 方案")


if __name__ == "__main__":
    unittest.main()
