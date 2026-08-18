from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import main
import shot_review
from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import ReviewRecorder
from shot_review import ensure_initial_prompt_versions, manual_prompt_editor
from storyboard import CreativeBrief, ShotVideoPrompt, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger


class FakeMiniMax:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def __call__(self, **kwargs):
        shot_id = int(kwargs["shot_id"])
        self.calls.append(shot_id)
        kwargs["on_submitted"](f"task-{shot_id}-{len(self.calls)}")
        kwargs["on_task_updated"](f"file-{shot_id}-{len(self.calls)}")
        kwargs["output_path"].write_bytes(f"video-{shot_id}".encode())
        return kwargs["output_path"]


def safety(prompt, *args, **kwargs):
    return PromptSafetyReview(
        is_safe=True, risk_notes=[], reviewed_video_prompt=f"SAFE::{prompt}"
    )


class PromptFileEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.request = ProductVideoRequest(
            product_name="P",
            product_description="D",
            duration_seconds=6,
            video_style="S",
            video_purpose="U",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "P", self.request.model_dump()
        )
        self.plan = VideoPromptPlan(
            shots=[ShotVideoPrompt(shot_id=1, video_prompt="red apple\nsoft light")]
        )
        self.logger = TaskLogger(self.paths, "editor-test")
        self.checkpoint.ensure_shots([1])
        ensure_initial_prompt_versions(
            self.paths, self.checkpoint, self.plan, self.logger
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_editor(self, editor, inputs):
        with patch("builtins.input", side_effect=inputs):
            return manual_prompt_editor(
                paths=self.paths,
                checkpoint=self.checkpoint,
                plan=self.plan,
                shot_id=1,
                task_logger=self.logger,
                editor=editor,
                cancel=Mock(side_effect=AssertionError("unexpected cancel")),
            )

    def test_A_single_word_creates_v2(self):
        def editor(path):
            self.assertEqual(path.read_text(encoding="utf-8"), "red apple\nsoft light")
            path.write_text("green apple\nsoft light", encoding="utf-8")

        self.assertTrue(self.run_editor(editor, ["1"]))
        payload = self.checkpoint.prompt_version(1, 2)
        self.assertEqual(payload["source"], "manual_edit")
        self.assertEqual(payload["parent_version"], 1)
        self.assertEqual(payload["original_prompt"], "red apple\nsoft light")
        self.assertEqual(payload["edited_prompt"], "green apple\nsoft light")
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["active_prompt_version"], 2)
        self.assertFalse(any(self.paths.shot_editing_dir(1).iterdir()))

    def test_B_only_one_line_changes_and_diff_is_shown(self):
        def editor(path):
            path.write_text("red apple\nmorning light", encoding="utf-8")

        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(self.run_editor(editor, ["1"]))
        payload = self.checkpoint.prompt_version(1, 2)
        self.assertEqual(payload["edited_prompt"].splitlines()[0], "red apple")
        self.assertIn("-soft light", output.getvalue())
        self.assertIn("+morning light", output.getvalue())

    def test_C_discard_keeps_v1_active(self):
        def editor(path):
            path.write_text("discarded edit", encoding="utf-8")

        self.assertFalse(self.run_editor(editor, ["3"]))
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["active_prompt_version"], 1)
        self.assertIsNone(self.checkpoint.prompt_version(1, 2))
        self.assertEqual(self.plan.shots[0].video_prompt, "red apple\nsoft light")

    def test_D_continue_reopens_first_edited_content(self):
        seen: list[str] = []

        def editor(path):
            seen.append(path.read_text(encoding="utf-8"))
            if len(seen) == 1:
                path.write_text("first edit", encoding="utf-8")
            else:
                path.write_text("second edit", encoding="utf-8")

        self.assertTrue(self.run_editor(editor, ["2", "1"]))
        self.assertEqual(seen, ["red apple\nsoft light", "first edit"])
        payload = self.checkpoint.prompt_version(1, 2)
        self.assertEqual(payload["edited_prompt"], "second edit")

    def test_E_F_G_confirmation_runs_safety_only_for_shot_two_and_no_llm(self):
        paths = create_project_paths(Path(self.temp.name) / "pipeline")
        request = ProductVideoRequest(
            product_name="P2",
            product_description="D",
            duration_seconds=18,
            video_style="S",
            video_purpose="U",
        )
        checkpoint = ProjectCheckpoint.create(paths, "P2", request.model_dump())
        brief = CreativeBrief(
            creative_concept="C",
            target_audience="A",
            key_message="K",
            visual_direction="V",
            narrative_arc="N",
        )
        board = {
            "total_duration": 18,
            "shots": [
                {"shot_id": i, "duration": 6, "purpose": "p", "visual": f"v{i}", "camera": "c"}
                for i in (1, 2, 3)
            ],
        }
        plan = VideoPromptPlan(
            shots=[ShotVideoPrompt(shot_id=i, video_prompt=f"prompt-{i}") for i in (1, 2, 3)]
        )
        paths.save_json(paths.creative_brief_path(), brief.model_dump())
        paths.save_json(paths.storyboard_file_path(), board)
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

        editor_calls = 0

        def editor(path):
            nonlocal editor_calls
            editor_calls += 1
            self.assertEqual(path.read_text(encoding="utf-8"), "prompt-2")
            path.write_text("manually edited shot 2", encoding="utf-8")

        minimax = FakeMiniMax()
        llm_reviser = Mock(side_effect=AssertionError("manual edit must not call LLM"))
        safety_calls: list[str] = []

        def tracked_safety(prompt, *args, **kwargs):
            safety_calls.append(prompt)
            return safety(prompt)

        with (
            patch.object(main, "generate_video", side_effect=minimax),
            patch.object(main, "review_prompt_safety", side_effect=tracked_safety),
            patch.object(main, "revise_shot_video_prompt", llm_reviser),
            patch.object(shot_review, "open_prompt_editor", side_effect=editor),
            patch("builtins.input", side_effect=["1", "4", "2", "1", "1", "1"]),
        ):
            main.run_pipeline(
                paths, request, checkpoint, "mock", "mock", TaskLogger(paths, "pipeline")
            )

        self.assertEqual(editor_calls, 1)
        self.assertEqual(minimax.calls, [1, 2, 2, 3])
        self.assertIn("manually edited shot 2", safety_calls)
        self.assertEqual(safety_calls.count("manually edited shot 2"), 1)
        self.assertEqual(checkpoint.shot_checkpoint(1)["generation_count"], 1)
        self.assertEqual(checkpoint.shot_checkpoint(2)["generation_count"], 2)
        self.assertEqual(checkpoint.shot_checkpoint(3)["generation_count"], 1)
        payload = checkpoint.prompt_version(2, 2)
        self.assertEqual(payload["safety_prompt"], "SAFE::manually edited shot 2")
        llm_reviser.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
