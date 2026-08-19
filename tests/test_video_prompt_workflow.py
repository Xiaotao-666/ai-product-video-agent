from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import ProductVideoRequest, PromptGenerationError
from storyboard import (
    CreativeBrief,
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    VideoPromptPlan,
    apply_video_overlay_constraints,
    generate_video_prompts,
    _video_prompt_progress_fingerprint,
)
from storyboard_workflow import approve_storyboard_stage
from task_logger import TaskLogger
from video_prompt_workflow import (
    VideoPromptApprovalError,
    VideoPromptStageStateError,
    approve_video_prompts_stage,
    generate_video_prompts_stage,
    regenerate_video_prompts_stage,
    revise_video_prompts_stage,
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

    def waiting_plan(self) -> VideoPromptPlan:
        plan = VideoPromptPlan(
            shots=[
                ShotVideoPrompt(
                    shot_id=shot.shot_id,
                    visual_prompt_core=f"core-{shot.shot_id}",
                    video_prompt=apply_video_overlay_constraints(
                        f"core-{shot.shot_id}",
                        shot,
                        self.brief.global_constraints,
                    ),
                )
                for shot in self.board.shots
            ]
        )
        self.paths.save_json(self.paths.video_prompts_path(), plan.model_dump())
        self.checkpoint.update_stage(
            ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED
        )
        self.checkpoint.advance_to(
            ProjectStage.PROMPT_REVIEW, StageStatus.WAITING_REVIEW
        )
        return plan

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

    def test_approval_initializes_formal_versions_without_mutating_content(self) -> None:
        plan = self.waiting_plan()
        progress = self.paths.video_prompt_generation_progress_path()
        progress.write_text('{"status":"COMPLETED"}\n', encoding="utf-8")
        canonical_before = self.paths.video_prompts_path().read_bytes()
        progress_before = progress.read_bytes()
        progress_mtime = progress.stat().st_mtime_ns

        with patch("prompt_generator.requests.post") as provider:
            approved = approve_video_prompts_stage(
                self.paths,
                self.checkpoint,
            )

        provider.assert_not_called()
        self.assertEqual(approved, plan)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
            StageStatus.APPROVED,
        )
        self.assertEqual(self.checkpoint.next_stage(), ProjectStage.VIDEO_GENERATION)
        self.assertEqual(self.checkpoint.current_stage, ProjectStage.PROMPT_REVIEW)
        for shot_id in (1, 2, 3):
            entry = self.checkpoint.shot_checkpoint(shot_id)
            self.assertEqual(entry["active_prompt_version"], 1)
            self.assertIsNone(entry["approved_prompt_version"])
            self.assertEqual(
                self.checkpoint.prompt_version(shot_id, 1)["prompt"],
                plan.shots[shot_id - 1].video_prompt,
            )
            self.assertIsNone(entry["active_video_version"])
        self.assertEqual(self.paths.video_prompts_path().read_bytes(), canonical_before)
        self.assertEqual(progress.read_bytes(), progress_before)
        self.assertEqual(progress.stat().st_mtime_ns, progress_mtime)
        self.assertFalse(any(self.paths.shots_dir.rglob("*.mp4")))

    def test_approval_preserves_mixed_matching_active_prompt_versions(self) -> None:
        plan = self.waiting_plan()
        self.checkpoint.ensure_shots([1, 2, 3])
        expected = {1: 2, 2: 1, 3: 3}
        for item in plan.shots:
            version = expected[item.shot_id]
            self.checkpoint.save_prompt_version(
                item.shot_id,
                {
                    "shot_id": item.shot_id,
                    "version": version,
                    "source": "ai_revision" if version > 1 else "ai_generated",
                    "created_at": "2026-08-19T00:00:00+08:00",
                    "prompt": item.video_prompt,
                    "parent_version": version - 1 if version > 1 else None,
                    "user_feedback": None,
                    "safety_prompt": None,
                    "safety_checked_at": None,
                },
            )

        approve_video_prompts_stage(self.paths, self.checkpoint)

        for shot_id, version in expected.items():
            entry = self.checkpoint.shot_checkpoint(shot_id)
            self.assertEqual(entry["active_prompt_version"], version)
            self.assertIsNone(entry["approved_prompt_version"])

    def test_approval_rejects_incomplete_duplicate_and_stale_pointer(self) -> None:
        complete = self.waiting_plan()
        original_project = self.checkpoint.path.read_bytes()
        invalid_plans = (
            VideoPromptPlan(shots=complete.shots[:2]),
            VideoPromptPlan(shots=[complete.shots[0], complete.shots[0], complete.shots[2]]),
        )
        for plan in invalid_plans:
            with self.subTest(ids=[item.shot_id for item in plan.shots]):
                self.paths.save_json(self.paths.video_prompts_path(), plan.model_dump())
                with self.assertRaises(VideoPromptApprovalError):
                    approve_video_prompts_stage(self.paths, self.checkpoint)
                self.assertEqual(self.checkpoint.path.read_bytes(), original_project)
                self.assertEqual(
                    self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
                    StageStatus.WAITING_REVIEW,
                )

        self.paths.save_json(self.paths.video_prompts_path(), complete.model_dump())
        self.checkpoint.ensure_shots([1, 2, 3])
        self.checkpoint.save_prompt_version(
            1,
            {
                "shot_id": 1,
                "version": 4,
                "source": "ai_revision",
                "created_at": "2026-08-19T00:00:00+08:00",
                "prompt": "stale unrelated prompt",
            },
        )
        with self.assertRaises(VideoPromptApprovalError):
            approve_video_prompts_stage(self.paths, self.checkpoint)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
            StageStatus.WAITING_REVIEW,
        )

    def test_cli_video_prompt_approval_calls_shared_core_entry(self) -> None:
        import main

        self.waiting_plan()

        class SharedApprovalReached(RuntimeError):
            pass

        def approve_from_gate(*_args, **kwargs):
            kwargs["on_approved"]()
            raise AssertionError("shared approval should have stopped the pipeline")

        with (
            patch.object(main, "human_review_gate", side_effect=approve_from_gate),
            patch.object(
                main,
                "approve_video_prompts_stage",
                side_effect=SharedApprovalReached,
            ) as shared,
            patch("prompt_generator.requests.post") as provider,
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
        shared.assert_called_once_with(self.paths, self.checkpoint, self.logger)
        provider.assert_not_called()

    def test_reset_from_video_prompt_clears_formal_pointers_for_resume(self) -> None:
        self.waiting_plan()
        progress = self.paths.video_prompt_generation_progress_path()
        progress.write_text('{"status":"COMPLETED"}\n', encoding="utf-8")
        approve_video_prompts_stage(self.paths, self.checkpoint)

        archived = self.checkpoint.reset_from(ProjectStage.VIDEO_PROMPT)

        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.VIDEO_PROMPT),
            StageStatus.NOT_STARTED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
            StageStatus.NOT_STARTED,
        )
        self.assertEqual(self.checkpoint.data["video_generation"]["shots"], {})
        self.assertFalse(self.paths.video_prompts_path().exists())
        self.assertFalse(progress.exists())
        self.assertEqual(len(archived), 2)

    def test_revision_is_per_shot_versioned_and_keeps_review_waiting(self) -> None:
        current = self.waiting_plan()
        seen: list[tuple[int, str | None, str | None]] = []

        def revise_one(_request, _brief, shot, *_args, **kwargs):
            seen.append(
                (
                    shot.shot_id,
                    kwargs.get("current_core"),
                    kwargs.get("revision_comment"),
                )
            )
            return f"revised-core-{shot.shot_id}"

        with (
            patch(
                "storyboard._request_single_shot_visual_core",
                side_effect=revise_one,
            ) as provider,
            patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("real network call"),
            ),
        ):
            updated = revise_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                current,
                " 减少运动并保持产品稳定 ",
                "mock-key",
                self.logger,
            )

        self.assertEqual(provider.call_count, 3)
        self.assertEqual(
            seen,
            [
                (1, "core-1", "减少运动并保持产品稳定"),
                (2, "core-2", "减少运动并保持产品稳定"),
                (3, "core-3", "减少运动并保持产品稳定"),
            ],
        )
        self.assertEqual(
            [item.visual_prompt_core for item in updated.shots],
            ["revised-core-1", "revised-core-2", "revised-core-3"],
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.PROMPT_REVIEW),
            StageStatus.WAITING_REVIEW,
        )
        for shot_id in (1, 2, 3):
            entry = self.checkpoint.shot_checkpoint(shot_id)
            self.assertEqual(entry["active_prompt_version"], 2)
            self.assertIsNone(entry["approved_prompt_version"])
            self.assertEqual(
                self.checkpoint.prompt_version(shot_id, 2)["source"],
                "ai_revision",
            )
        progress = json.loads(
            self.paths.video_prompt_generation_progress_path().read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(progress["operation"], "revise")
        self.assertEqual(progress["status"], "PUBLISHED")
        self.assertNotIn("减少运动", json.dumps(progress, ensure_ascii=False))
        self.assertFalse(any(self.paths.shots_dir.rglob("*.mp4")))

    def test_revision_failure_keeps_canonical_and_resumes_completed_shot(self) -> None:
        current = self.waiting_plan()
        canonical_before = self.paths.video_prompts_path().read_bytes()
        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=["revised-core-1", PromptGenerationError("temporary")],
        ) as first:
            with self.assertRaises(PromptGenerationError):
                revise_video_prompts_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    current,
                    "减少运动",
                    "mock-key",
                    self.logger,
                )
        self.assertEqual(first.call_count, 2)
        self.assertEqual(self.paths.video_prompts_path().read_bytes(), canonical_before)

        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=["revised-core-2", "revised-core-3"],
        ) as resumed:
            revise_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                current,
                "减少运动",
                "mock-key",
                self.logger,
            )
        self.assertEqual(resumed.call_count, 2)

    def test_regenerate_uses_no_old_prompt_and_new_action_does_not_reuse(self) -> None:
        current = self.waiting_plan()
        seen: list[tuple[str | None, str | None]] = []

        def regenerate_one(_request, _brief, shot, *_args, **kwargs):
            seen.append(
                (kwargs.get("current_core"), kwargs.get("revision_comment"))
            )
            return f"fresh-{shot.shot_id}-{len(seen)}"

        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=regenerate_one,
        ) as provider:
            regenerate_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
            regenerate_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        self.assertEqual(provider.call_count, 6)
        self.assertEqual(seen, [(None, None)] * 6)
        for shot_id in (1, 2, 3):
            entry = self.checkpoint.shot_checkpoint(shot_id)
            self.assertEqual(entry["active_prompt_version"], 3)
            self.assertIsNone(entry["approved_prompt_version"])

    def test_revision_fingerprint_distinguishes_feedback_and_regenerate(self) -> None:
        current = self.waiting_plan()
        first = _video_prompt_progress_fingerprint(
            self.request,
            self.brief,
            self.board,
            operation="revise",
            current=current,
            revision_comment="减少运动",
        )
        second = _video_prompt_progress_fingerprint(
            self.request,
            self.brief,
            self.board,
            operation="revise",
            current=current,
            revision_comment="增加运动",
        )
        regenerated = _video_prompt_progress_fingerprint(
            self.request,
            self.brief,
            self.board,
            operation="regenerate",
            current=current,
        )
        initial = _video_prompt_progress_fingerprint(
            self.request,
            self.brief,
            self.board,
        )
        self.assertEqual(len({first, second, regenerated, initial}), 4)

    def test_revision_increments_each_shot_from_its_own_version_history(self) -> None:
        current = self.waiting_plan()
        current_versions = {1: 2, 2: 1, 3: 4}
        self.checkpoint.ensure_shots([1, 2, 3])
        for item in current.shots:
            version = current_versions[item.shot_id]
            self.checkpoint.save_prompt_version(
                item.shot_id,
                {
                    "shot_id": item.shot_id,
                    "version": version,
                    "source": "ai_generated",
                    "created_at": "2026-08-19T00:00:00+08:00",
                    "prompt": item.video_prompt,
                    "parent_version": None,
                    "user_feedback": None,
                    "safety_prompt": None,
                    "safety_checked_at": None,
                },
            )
        with patch(
            "storyboard._request_single_shot_visual_core",
            side_effect=["new-1", "new-2", "new-3"],
        ):
            revise_video_prompts_stage(
                self.paths,
                self.request,
                self.checkpoint,
                current,
                "分别更新",
                "mock-key",
                self.logger,
            )
        self.assertEqual(
            {
                shot_id: self.checkpoint.shot_checkpoint(shot_id)[
                    "active_prompt_version"
                ]
                for shot_id in (1, 2, 3)
            },
            {1: 3, 2: 2, 3: 5},
        )


if __name__ == "__main__":
    unittest.main()
