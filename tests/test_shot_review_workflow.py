from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
import shot_review
from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStage, ShotStatus, StageStatus
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import TaskCancelled
from shot_review import ensure_initial_prompt_versions, save_safety_to_active_prompt
from storyboard import (
    CreativeBrief,
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    VideoPromptPlan,
)
from task_logger import TaskLogger


def safe_review(prompt: str, *args, **kwargs) -> PromptSafetyReview:
    return PromptSafetyReview(
        is_safe=True, risk_notes=[], reviewed_video_prompt=f"SAFE::{prompt}"
    )


class FakeMiniMax:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> Path:
        shot_id = int(kwargs["shot_id"])
        call_number = len(self.calls) + 1
        resume_task = kwargs.get("resume_task")
        resume_task_id = (
            resume_task.provider_task_id if resume_task is not None else None
        )
        resume_file_id = (
            resume_task.provider_file_id if resume_task is not None else None
        )
        self.calls.append(
            {
                "shot_id": shot_id,
                "prompt": kwargs["prompt"],
                "resume_task_id": resume_task_id,
                "resume_file_id": resume_file_id,
            }
        )
        task_id = resume_task_id
        if not task_id:
            task_id = f"task-{shot_id}-{call_number}"
            kwargs["on_submitted"](task_id)
        file_id = resume_file_id
        if not file_id:
            file_id = f"file-{shot_id}-{call_number}"
            kwargs["on_task_updated"](file_id)
        output = kwargs["output_path"]
        output.write_bytes(f"video-shot-{shot_id}-call-{call_number}".encode())
        return output


class ShotWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ready_project(self, name: str, shot_count: int):
        paths = create_project_paths(Path(self.temp.name) / name)
        request = ProductVideoRequest(
            product_name=name,
            product_description="产品说明",
            duration_seconds=shot_count * 6,
            video_style="清新统一",
            video_purpose="品牌宣传",
        )
        checkpoint = ProjectCheckpoint.create(paths, name, request.model_dump())
        brief = CreativeBrief(
            creative_concept="概念",
            target_audience="受众",
            key_message="信息",
            visual_direction="统一自然光与产品外观",
            narrative_arc="逐步展示",
        )
        board = Storyboard(
            total_duration=shot_count * 6,
            shots=[
                StoryboardShot(
                    shot_id=i,
                    duration=6,
                    purpose=f"目的{i}",
                    visual=f"画面{i}",
                    camera=f"运镜{i}",
                )
                for i in range(1, shot_count + 1)
            ],
        )
        plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(shot_id=i, video_prompt=f"prompt-{i}")
                for i in range(1, shot_count + 1)
            ]
        )
        paths.save_json(paths.creative_brief_path(), brief.model_dump())
        paths.save_json(paths.storyboard_file_path(), board.model_dump())
        paths.save_json(paths.video_prompts_path(), plan.model_dump())
        for stage, status in (
            (ProjectStage.CREATIVE, StageStatus.COMPLETED),
            (ProjectStage.CREATIVE_REVIEW, StageStatus.APPROVED),
            (ProjectStage.STORYBOARD, StageStatus.COMPLETED),
            (ProjectStage.STORYBOARD_REVIEW, StageStatus.APPROVED),
            (ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED),
            (ProjectStage.PROMPT_REVIEW, StageStatus.APPROVED),
        ):
            checkpoint.update_stage(stage, status)
        return paths, request, checkpoint, brief, board, plan

    def run_ready(self, name: str, shot_count: int, inputs: list[str], *, reviser=None):
        data = self.ready_project(name, shot_count)
        paths, request, checkpoint, _brief, _board, _plan = data
        minimax = FakeMiniMax()
        task_logger = TaskLogger(paths, task_id=f"test-{name}")
        revision = reviser or Mock(return_value="AI revised prompt")
        with (
            patch.object(main, "generate_video", side_effect=minimax),
            patch.object(main, "review_prompt_safety", side_effect=safe_review),
            patch.object(main, "revise_shot_video_prompt", revision),
            patch("builtins.input", side_effect=inputs),
        ):
            main.run_pipeline(
                paths, request, checkpoint, "deepseek-mock", "minimax-mock", task_logger
            )
        return data, minimax, task_logger, revision

    def initialize_generation(self, data):
        paths, _request, checkpoint, _brief, board, plan = data
        logger = TaskLogger(paths, task_id="setup")
        checkpoint.ensure_shots([shot.shot_id for shot in board.shots])
        ensure_initial_prompt_versions(paths, checkpoint, plan, logger)
        checkpoint.update_stage(ProjectStage.VIDEO_GENERATION, StageStatus.RUNNING)
        return logger

    def test_A_normal_three_shots(self):
        data, minimax, *_ = self.run_ready("A", 3, ["1", "1", "1"])
        paths, _request, checkpoint, *_ = data
        self.assertEqual([c["shot_id"] for c in minimax.calls], [1, 2, 3])
        self.assertTrue(checkpoint.all_shots_approved([1, 2, 3]))
        self.assertEqual(checkpoint.status, StageStatus.COMPLETED.value)
        self.assertTrue(all(paths.shot_version_video_path(i, 1).is_file() for i in (1, 2, 3)))

    def test_B_regenerate_same_prompt_only_current_shot(self):
        data, minimax, *_ = self.run_ready("B", 3, ["1", "2", "1", "1"])
        paths, _request, checkpoint, *_ = data
        self.assertEqual([c["shot_id"] for c in minimax.calls], [1, 2, 2, 3])
        self.assertEqual(checkpoint.shot_checkpoint(2)["generation_count"], 2)
        self.assertTrue(paths.shot_version_video_path(2, 1).is_file())
        self.assertTrue(paths.shot_version_video_path(2, 2).is_file())
        self.assertTrue(paths.shot_version_video_path(1, 1).is_file())

    def test_C_ai_revision_changes_only_shot_two_and_requires_safety(self):
        reviser = Mock(return_value="AI revised shot 2")
        data, minimax, _logger, _ = self.run_ready(
            "C", 3, ["1", "3", "slower", "1", "1", "1"], reviser=reviser
        )
        paths, _request, checkpoint, _brief, _board, _plan = data
        saved_plan = VideoPromptPlan.model_validate(
            json.loads(paths.video_prompts_path().read_text(encoding="utf-8"))
        )
        self.assertEqual([c["shot_id"] for c in minimax.calls], [1, 2, 2, 3])
        self.assertEqual([p.video_prompt for p in saved_plan.shots], ["prompt-1", "AI revised shot 2", "prompt-3"])
        payload = checkpoint.prompt_version(2, 2)
        self.assertEqual(payload["source"], "ai_revision")
        self.assertEqual(payload["safety_prompt"], "SAFE::AI revised shot 2")
        self.assertEqual(checkpoint.shot_checkpoint(2)["active_prompt_version"], 2)
        reviser.assert_called_once()

    def test_D_manual_multiline_edit_skips_ai_and_uses_safety(self):
        reviser = Mock(side_effect=AssertionError("manual edit must not call LLM reviser"))

        def edit_existing_prompt(path):
            self.assertEqual(path.read_text(encoding="utf-8"), "prompt-1")
            path.write_text("line one\nline two", encoding="utf-8")

        with patch.object(shot_review, "open_prompt_editor", side_effect=edit_existing_prompt):
            data, minimax, _logger, _ = self.run_ready(
                "D", 1, ["4", "2", "1", "1"], reviser=reviser
            )
        paths, _request, checkpoint, *_ = data
        payload = checkpoint.prompt_version(1, 2)
        self.assertEqual(payload["source"], "manual_edit")
        self.assertEqual(payload["prompt"], "line one\nline two")
        self.assertEqual(payload["safety_prompt"], "SAFE::line one\nline two")
        self.assertEqual([c["shot_id"] for c in minimax.calls], [1, 1])
        self.assertEqual(checkpoint.shot_checkpoint(1)["generation_count"], 2)
        reviser.assert_not_called()

    def test_E_resume_waiting_review_does_not_call_minimax(self):
        data = self.ready_project("E", 1)
        paths, request, checkpoint, *_ = data
        self.initialize_generation(data)
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(1, "waiting-task")
        checkpoint.mark_shot_file_ready(1, "waiting-file")
        paths.shot_version_video_path(1, 1).write_bytes(b"existing")
        checkpoint.mark_shot_ready_for_review(1)
        forbidden = Mock(side_effect=AssertionError("MiniMax must not be called"))
        with (
            patch.object(main, "generate_video", forbidden),
            patch.object(main, "review_prompt_safety", side_effect=safe_review),
            patch("builtins.input", side_effect=["1"]),
        ):
            main.run_pipeline(paths, request, checkpoint, "mock", "mock", TaskLogger(paths, "E-resume"))
        forbidden.assert_not_called()
        self.assertEqual(checkpoint.shot_status(1), ShotStatus.APPROVED)

    def test_F_resume_generating_reuses_existing_task_id(self):
        data = self.ready_project("F", 1)
        paths, request, checkpoint, *_rest, plan = data
        logger = self.initialize_generation(data)
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(1, "existing-task-id")
        save_safety_to_active_prompt(paths, checkpoint, plan, 1, safe_review("prompt-1"))
        minimax = FakeMiniMax()
        with (
            patch.object(main, "generate_video", side_effect=minimax),
            patch.object(main, "review_prompt_safety", side_effect=AssertionError("saved safety should resume")),
            patch("builtins.input", side_effect=["1"]),
        ):
            main.run_pipeline(paths, request, checkpoint, "mock", "mock", logger)
        self.assertEqual(len(minimax.calls), 1)
        self.assertEqual(minimax.calls[0]["resume_task_id"], "existing-task-id")
        self.assertEqual(checkpoint.shot_checkpoint(1)["generation_count"], 1)

    def test_G_approved_shot_is_completely_skipped(self):
        data = self.ready_project("G", 1)
        paths, request, checkpoint, *_ = data
        self.initialize_generation(data)
        checkpoint.prepare_shot_generation(1)
        checkpoint.mark_shot_submitted(1, "approved-task")
        checkpoint.mark_shot_file_ready(1, "approved-file")
        paths.shot_version_video_path(1, 1).write_bytes(b"approved")
        checkpoint.mark_shot_ready_for_review(1)
        checkpoint.approve_shot(1)
        forbidden = Mock(side_effect=AssertionError("approved Shot must be skipped"))
        with patch.object(main, "generate_video", forbidden):
            main.run_pipeline(paths, request, checkpoint, "mock", "mock", TaskLogger(paths, "G-resume"))
        forbidden.assert_not_called()

    def test_H_cancel_at_shot_two_stops_later_shots(self):
        data = self.ready_project("H", 3)
        paths, request, checkpoint, *_ = data
        minimax = FakeMiniMax()
        logger = TaskLogger(paths, "H-cancel")
        with (
            patch.object(main, "generate_video", side_effect=minimax),
            patch.object(main, "review_prompt_safety", side_effect=safe_review),
            patch("builtins.input", side_effect=["1", "6"]),
        ):
            with self.assertRaises(TaskCancelled):
                main.run_pipeline(paths, request, checkpoint, "mock", "mock", logger)
        self.assertEqual([c["shot_id"] for c in minimax.calls], [1, 2])
        self.assertFalse(paths.shot_version_video_path(3, 1).exists())
        self.assertEqual(checkpoint.status, StageStatus.CANCELLED.value)
        self.assertEqual(checkpoint.data["cancel_shot_id"], 2)
        self.assertIn("TASK_CANCELLED", logger.task_log_path.read_text(encoding="utf-8"))

    def test_I_schema2_project_uses_only_project_schema_version(self):
        data = self.ready_project("I", 1)
        paths, _request, checkpoint, *_ = data
        saved = json.loads(paths.project_state_path().read_text(encoding="utf-8"))
        self.assertEqual(saved["project_schema_version"], 2)
        self.assertNotIn("schema_version", saved)

    def test_J_three_generations_preserve_two_archives(self):
        data, minimax, *_ = self.run_ready("J", 1, ["2", "2", "1"])
        paths, _request, checkpoint, *_ = data
        entry = checkpoint.shot_checkpoint(1)
        self.assertEqual(len(minimax.calls), 3)
        self.assertEqual(entry["generation_count"], 3)
        self.assertEqual(entry["active_video_version"], 3)
        self.assertEqual(entry["active_prompt_version"], 1)
        self.assertTrue(paths.shot_version_video_path(1, 1).is_file())
        self.assertTrue(paths.shot_version_video_path(1, 2).is_file())
        self.assertIn(b"call-3", paths.shot_version_video_path(1, 3).read_bytes())
        task_ids = [v["provider_task_id"] for v in entry["generation_versions"]]
        self.assertEqual(len(task_ids), len(set(task_ids)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
