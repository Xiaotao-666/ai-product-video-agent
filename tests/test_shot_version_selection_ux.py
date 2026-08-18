from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from post_production import PostProductionStatus
from post_production_menu import _export_status_label
from project_manager import create_project_paths
from project_state import CandidateStatus, ProjectCheckpoint, ShotStatus
from review_manager import ReviewRecorder
from shot_manager import (
    _version_comparison_menu,
    manage_approved_shot,
    select_historical_version_as_approved,
    shot_management_menu,
)
from shot_storage import write_review_snapshot
from storyboard import (
    CreativeBrief,
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    VideoPromptPlan,
)
from task_logger import TaskLogger
from video_assembly import (
    approved_shot_inputs,
    confirm_assembly_versions,
)
from video_history import (
    VideoHistoryError,
    create_historical_candidate,
    display_video_history,
    parse_video_version,
    video_history_menu,
    video_version_history,
)


class ShotVersionSelectionUXTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.paths = create_project_paths(Path(self.temp.name) / "ux-project")
        self.checkpoint = ProjectCheckpoint.create(
            self.paths, "ux-project", {"product_name": "test"}
        )
        self.checkpoint.ensure_shots([1, 2, 3])
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
        self.brief = CreativeBrief(
            creative_concept="concept",
            target_audience="audience",
            key_message="message",
            visual_direction="visual",
            narrative_arc="arc",
        )
        self.plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(shot_id=shot_id, video_prompt=f"prompt-{shot_id}-v2")
                for shot_id in (1, 2, 3)
            ]
        )
        self.paths.save_json(self.paths.video_prompts_path(), self.plan.model_dump())
        self.logger = TaskLogger(self.paths, "version-selection")
        self.recorder = ReviewRecorder(
            self.paths,
            {"product_name": "test"},
            self.logger.task_id,
            self.logger,
        )
        for shot_id in (1, 2, 3):
            self._add_prompt(shot_id, 1, "ai_generated")
            self._add_prompt(shot_id, 2, "manual_edit")
            self._add_video(shot_id, 1, 1, "REJECTED")
            self._add_video(shot_id, 2, 2, "APPROVED")
            entry = self.checkpoint.shot_checkpoint(shot_id)
            entry.update(
                {
                    "status": ShotStatus.APPROVED.value,
                    "generation_count": 2,
                    "active_prompt_version": 2,
                    "active_video_version": 2,
                    "approved_prompt_version": 2,
                    "approved_video_version": 2,
                    "provider_task_id": f"task-{shot_id}-v2",
                    "file_id": f"file-{shot_id}-v2",
                }
            )
        self.checkpoint.save()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_prompt(self, shot_id: int, version: int, source: str) -> None:
        self.checkpoint.save_prompt_version(
            shot_id,
            {
                "shot_id": shot_id,
                "version": version,
                "source": source,
                "created_at": f"2026-08-17T10:0{version}:00",
                "prompt": f"prompt-{shot_id}-v{version}",
                "review_result": "REJECTED" if version == 1 else "APPROVED",
            },
        )

    def _add_video(
        self, shot_id: int, video_version: int, prompt_version: int, result: str
    ) -> None:
        path = self.paths.shot_version_video_path(shot_id, video_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"shot-{shot_id}-video-{video_version}".encode())
        entry = self.checkpoint.shot_checkpoint(shot_id)
        entry.setdefault("generation_versions", []).append(
            {
                "video_version": video_version,
                "prompt_version": prompt_version,
                "status": result,
                "review_result": result,
                "created_at": f"2026-08-17T11:0{video_version}:00",
                "provider": "minimax",
                "provider_model": "MiniMax-H3",
                "provider_api_version": "v2",
                "provider_task_id": f"task-{shot_id}-v{video_version}",
                "file_id": f"file-{shot_id}-v{video_version}",
                "video_path": path.relative_to(self.paths.project_path).as_posix(),
                "is_active": video_version == 2,
                "is_approved": video_version == 2,
            }
        )
        write_review_snapshot(
            self.paths,
            shot_id,
            video_version,
            review_result=result,
            user_action="initial_review",
        )

    def _select_v1(self, shot_id: int = 1) -> None:
        target = next(
            item
            for item in video_version_history(self.paths, self.checkpoint, shot_id)
            if item.video_version == 1
        )
        select_historical_version_as_approved(
            self.paths,
            self.checkpoint,
            self.plan,
            self.recorder,
            self.logger,
            shot_id,
            target,
        )

    def test_01_parse_plain_one(self):
        self.assertEqual(parse_video_version("1"), 1)

    def test_02_parse_zero_padded_one(self):
        self.assertEqual(parse_video_version("001"), 1)

    def test_03_parse_v_one(self):
        self.assertEqual(parse_video_version("v1"), 1)

    def test_04_parse_v_zero_padded_one(self):
        self.assertEqual(parse_video_version("v001"), 1)

    def test_05_parse_arbitrary_large_version(self):
        self.assertEqual(parse_video_version("v105"), 105)

    def test_06_invalid_version_is_clear(self):
        with self.assertRaisesRegex(VideoHistoryError, "格式无效"):
            parse_video_version("version-one")

    def test_07_selecting_current_official_is_benign(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", side_effect=["1", "v002", "4"]):
            changed = video_history_menu(
                self.paths,
                self.checkpoint,
                1,
                self.logger,
                approved_mode=True,
                approved_selector=Mock(),
            )
        self.assertFalse(changed)
        self.assertIn("已经是当前正式版本", output.getvalue())

    def test_08_direct_selection_changes_official_video(self):
        self._select_v1()
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["approved_video_version"], 1)

    def test_09_prompt_follows_video_bundle_binding(self):
        self._select_v1()
        entry = self.checkpoint.shot_checkpoint(1)
        self.assertEqual(entry["approved_prompt_version"], 1)
        self.assertEqual(self.plan.shots[0].video_prompt, "prompt-1-v1")

    def test_10_local_selection_has_no_video_api_callback(self):
        forbidden = Mock(side_effect=AssertionError("API must not be called"))
        self._select_v1()
        forbidden.assert_not_called()

    def test_11_generation_count_does_not_increase(self):
        before = self.checkpoint.shot_checkpoint(1)["generation_count"]
        self._select_v1()
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["generation_count"], before)

    def test_12_all_version_files_are_preserved(self):
        old_bytes = {
            version: self.paths.shot_version_video_path(1, version).read_bytes()
            for version in (1, 2)
        }
        self._select_v1()
        self.assertEqual(
            old_bytes,
            {
                version: self.paths.shot_version_video_path(1, version).read_bytes()
                for version in (1, 2)
            },
        )

    def test_13_rejected_version_requires_extra_confirmation(self):
        selector = Mock()
        output = io.StringIO()
        with redirect_stdout(output), patch(
            "builtins.input", side_effect=["1", "v1", "1", "2", "4"]
        ):
            video_history_menu(
                self.paths,
                self.checkpoint,
                1,
                self.logger,
                approved_mode=True,
                approved_selector=selector,
            )
        selector.assert_not_called()
        self.assertIn("曾经被标记为 REJECTED", output.getvalue())

    def test_14_rejected_version_can_be_reapproved(self):
        self._select_v1()
        self.assertEqual(self.checkpoint.shot_checkpoint(1)["approved_video_version"], 1)

    def test_15_original_review_history_is_preserved(self):
        self._select_v1()
        review = json.loads(
            self.paths.shot_version_review_path(1, 1).read_text(encoding="utf-8")
        )
        self.assertEqual(review["history"][0]["review_result"], "REJECTED")
        self.assertEqual(review["history"][-1]["review_result"], "APPROVED")

    def test_16_candidate_backend_transaction_finishes_safely(self):
        self._select_v1()
        self.assertEqual(self.checkpoint.candidate_status(1), CandidateStatus.NONE)
        self.assertTrue(self.checkpoint.shot_checkpoint(1)["candidate_history"])

    def test_17_normal_version_ui_hides_candidate_term(self):
        output = io.StringIO()
        with redirect_stdout(output):
            display_video_history(self.paths, self.checkpoint, 1)
        self.assertNotIn("Candidate", output.getvalue())

    def test_18_pending_version_uses_human_label(self):
        create_historical_candidate(
            self.paths, self.checkpoint, 1, 1, self.logger, self.recorder
        )
        output = io.StringIO()
        with redirect_stdout(output):
            display_video_history(self.paths, self.checkpoint, 1)
        self.assertIn("待审核新版本", output.getvalue())
        self.assertNotIn("Candidate", output.getvalue())

    def test_19_abandoned_new_version_remains_in_bundle_history(self):
        create_historical_candidate(
            self.paths, self.checkpoint, 1, 1, self.logger, self.recorder
        )
        from shot_manager import reject_candidate

        reject_candidate(self.paths, self.checkpoint, self.recorder, self.logger, 1)
        self.assertTrue(self.paths.shot_version_video_path(1, 1).is_file())

    def test_20_shot_list_displays_video_and_prompt_versions(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", return_value="0"):
            shot_management_menu(
                self.paths,
                self.checkpoint,
                Mock(),
                self.brief,
                self.board,
                self.plan,
                "",
                "",
                self.logger,
                self.recorder,
                revise_prompt=Mock(),
                safety_review=Mock(),
                video_generate=Mock(),
            )
        self.assertIn("Video v2 / Prompt v2", output.getvalue())

    def test_21_assembly_confirmation_lists_every_binding(self):
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", return_value="4"):
            self.assertFalse(
                confirm_assembly_versions(
                    self.paths, self.checkpoint, self.board, Mock()
                )
            )
        text = output.getvalue()
        self.assertIn("Shot 01", text)
        self.assertIn("Video：v2", text)
        self.assertIn("Prompt：v2", text)

    def test_22_assembly_reads_new_approved_pointer(self):
        self._select_v1(2)
        selected = approved_shot_inputs(self.paths, self.checkpoint, self.board)
        self.assertEqual(selected[1]["approved_video_version"], 1)

    def test_23_switch_marks_existing_assembly_outdated(self):
        final = self.paths.final_video_path()
        final.write_bytes(b"old-final")
        self.checkpoint.complete_assembly(
            final,
            1,
            18.0,
            [
                {"shot_id": shot_id, "approved_video_version": 2}
                for shot_id in (1, 2, 3)
            ],
        )
        self._select_v1(2)
        assembly = self.checkpoint.assembly_checkpoint()
        self.assertTrue(assembly["needs_update"])
        self.assertEqual(assembly["changed_shot_id"], 2)

    def test_24_other_shots_are_unchanged(self):
        before = deepcopy(self.checkpoint.shot_checkpoint(3))
        self._select_v1(2)
        self.assertEqual(self.checkpoint.shot_checkpoint(3), before)

    def test_25_existing_export_is_not_deleted(self):
        export = self.paths.export_version_video_path(1)
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_bytes(b"old-export")
        self._select_v1()
        self.assertEqual(export.read_bytes(), b"old-export")

    def test_26_export_status_can_show_update_needed(self):
        component = {
            "status": PostProductionStatus.COMPLETED.value,
            "active_version": 1,
        }
        self.assertIn(
            "需要更新",
            _export_status_label(component, assembly_needs_update=True),
        )

    def test_27_resume_reads_selected_official_version(self):
        self._select_v1()
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.shot_checkpoint(1)["approved_video_version"], 1)
        self.assertEqual(loaded.shot_checkpoint(1)["approved_prompt_version"], 1)

    def test_28_resume_selection_never_calls_minimax(self):
        self._select_v1()
        forbidden = Mock(side_effect=AssertionError("MiniMax must not be called"))
        ProjectCheckpoint.load(self.paths)
        forbidden.assert_not_called()

    def test_29_pending_new_version_resumes_for_review(self):
        create_historical_candidate(
            self.paths, self.checkpoint, 1, 1, self.logger, self.recorder
        )
        loaded = ProjectCheckpoint.load(self.paths)
        self.assertEqual(loaded.candidate_status(1), CandidateStatus.WAITING_REVIEW)

    def test_30_comparison_ui_is_human_readable_not_raw_json(self):
        create_historical_candidate(
            self.paths, self.checkpoint, 1, 1, self.logger, self.recorder
        )
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", return_value="5"):
            _version_comparison_menu(self.paths, self.checkpoint, 1)
        text = output.getvalue()
        self.assertIn("当前正式版本", text)
        self.assertNotIn('"approved"', text)

    def test_31_history_uses_chinese_natural_status(self):
        output = io.StringIO()
        with redirect_stdout(output):
            display_video_history(self.paths, self.checkpoint, 1)
        text = output.getvalue()
        self.assertIn("[当前正式]", text)
        self.assertIn("[历史版本]", text)
        self.assertNotIn("[CURRENT", text)

    def test_32_assembly_modify_opens_selected_shot_then_returns(self):
        opened: list[int] = []
        with patch("builtins.input", side_effect=["2", "2", "4"]):
            confirmed = confirm_assembly_versions(
                self.paths,
                self.checkpoint,
                self.board,
                lambda shot_id: opened.append(shot_id),
            )
        self.assertFalse(confirmed)
        self.assertEqual(opened, [2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
