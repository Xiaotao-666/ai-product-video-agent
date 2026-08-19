from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


class CreativeWorkflowExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint
        from prompt_generator import ProductVideoRequest
        from task_logger import TaskLogger

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = create_project_paths(Path(self.temp.name) / "project")
        self.request = ProductVideoRequest(
            product_name="测试产品",
            product_description="产品事实",
            user_notes="不要出现人物",
            duration_seconds=18,
            video_style="清爽",
            video_purpose="产品宣传",
        )
        self.checkpoint = ProjectCheckpoint.create(
            self.paths,
            self.request.product_name,
            self.request.model_dump(),
        )
        self.logger = TaskLogger(self.paths, task_id="core_creative_test")

    @staticmethod
    def brief():
        from tests.web.test_backend_phase_3a2_creative_generate import creative_brief

        return creative_brief()

    def test_01_shared_callable_persists_core_artifact_and_review_state(self):
        from creative_workflow import generate_creative_stage

        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=self.brief(),
        ) as provider:
            result = generate_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        provider.assert_called_once()
        self.assertEqual(result.creative_concept, self.brief().creative_concept)
        saved = json.loads(
            self.paths.creative_brief_path().read_text(encoding="utf-8")
        )
        self.assertEqual(saved["creative_concept"], self.brief().creative_concept)
        self.assertEqual(
            self.checkpoint.data["stages"]["CREATIVE_REVIEW"]["status"],
            "WAITING_REVIEW",
        )

    def test_02_shared_callable_passes_product_request_and_reference_context(self):
        from creative_workflow import generate_creative_stage

        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=self.brief(),
        ) as provider:
            generate_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
                reference_asset_context={
                    "available": True,
                    "asset_count": 1,
                    "asset_ids": ["asset-1"],
                },
            )
        self.assertIs(provider.call_args.args[0], self.request)
        self.assertEqual(
            provider.call_args.kwargs["reference_asset_context"]["asset_ids"],
            ["asset-1"],
        )

    def test_03_cli_run_pipeline_calls_the_same_shared_creative_callable(self):
        import main

        class SharedCallableReached(RuntimeError):
            pass

        with patch.object(
            main,
            "generate_creative_stage",
            side_effect=SharedCallableReached,
        ) as shared:
            with self.assertRaises(SharedCallableReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once()

    def test_04_shared_approve_persists_review_and_resumes_at_storyboard(self):
        from creative_workflow import approve_creative_stage
        from project_state import ProjectStage, StageStatus

        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
        self.checkpoint.advance_to(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.WAITING_REVIEW,
        )
        approve_creative_stage(self.checkpoint)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW),
            StageStatus.APPROVED,
        )
        self.assertEqual(self.checkpoint.next_stage(), ProjectStage.STORYBOARD)
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.STORYBOARD),
            StageStatus.NOT_STARTED,
        )

    def test_05_shared_approve_rejects_invalid_and_repeated_state(self):
        from creative_workflow import CreativeApprovalError, approve_creative_stage
        from project_state import ProjectStage, StageStatus

        with self.assertRaises(CreativeApprovalError):
            approve_creative_stage(self.checkpoint)

        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
        self.checkpoint.advance_to(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.WAITING_REVIEW,
        )
        approve_creative_stage(self.checkpoint)
        with self.assertRaises(CreativeApprovalError):
            approve_creative_stage(self.checkpoint)

    def test_06_cli_review_callback_uses_shared_approve_callable(self):
        import main
        from project_state import ProjectStage, StageStatus

        class SharedApproveReached(RuntimeError):
            pass

        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
        self.checkpoint.advance_to(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.WAITING_REVIEW,
        )
        self.paths.save_json(
            self.paths.creative_brief_path(),
            self.brief().model_dump(),
        )

        def approve_from_gate(*_args, **kwargs):
            kwargs["on_approved"]()
            self.fail("CLI should stop at the patched shared approve callable")

        with (
            patch.object(main, "human_review_gate", side_effect=approve_from_gate),
            patch.object(
                main,
                "approve_creative_stage",
                side_effect=SharedApproveReached,
            ) as shared,
        ):
            with self.assertRaises(SharedApproveReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )
        shared.assert_called_once_with(self.checkpoint)

    def mark_waiting_review(self) -> None:
        from project_state import ProjectStage, StageStatus

        self.paths.save_json(
            self.paths.creative_brief_path(),
            self.brief().model_dump(),
        )
        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
        self.checkpoint.advance_to(
            ProjectStage.CREATIVE_REVIEW,
            StageStatus.WAITING_REVIEW,
        )

    def test_07_shared_revise_uses_current_feedback_and_preserves_review_state(self):
        from creative_workflow import revise_creative_stage
        from evaluation import EvaluationRecorder

        self.mark_waiting_review()
        updated = self.brief().model_copy(
            update={"creative_concept": "保留主题后的产品微距"}
        )
        recorder = EvaluationRecorder(self.paths)
        with patch(
            "creative_workflow.revise_creative_brief",
            return_value=updated,
        ) as provider:
            result = revise_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.brief(),
                "  保留主题，不要人物  ",
                "mock-key",
                self.logger,
                evaluation_recorder=recorder,
                reference_asset_context={"available": True, "asset_count": 1},
            )
        self.assertEqual(result.creative_concept, "保留主题后的产品微距")
        self.assertIs(provider.call_args.args[0], self.request)
        self.assertEqual(provider.call_args.args[1], self.brief())
        self.assertEqual(provider.call_args.args[2], "保留主题，不要人物")
        self.assertEqual(
            self.checkpoint.data["stages"]["CREATIVE_REVIEW"]["status"],
            "WAITING_REVIEW",
        )
        history = json.loads(
            self.paths.evaluation_prompt_path("creative").read_text(encoding="utf-8")
        )["records"][-1]
        self.assertEqual(history["operation"], "revise")
        self.assertEqual(history["input_fields"]["user_feedback"], "保留主题，不要人物")
        self.assertEqual(
            history["input_fields"]["current_output"]["creative_concept"],
            self.brief().creative_concept,
        )

    def test_08_shared_regenerate_uses_original_request_without_old_creative(self):
        from creative_workflow import regenerate_creative_stage
        from evaluation import EvaluationRecorder

        self.mark_waiting_review()
        replacement = self.brief().model_copy(
            update={"creative_concept": "基于原始需求的全新方案"}
        )
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=replacement,
        ) as provider:
            result = regenerate_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
                evaluation_recorder=EvaluationRecorder(self.paths),
            )
        self.assertEqual(result.creative_concept, "基于原始需求的全新方案")
        self.assertIs(provider.call_args.args[0], self.request)
        self.assertNotIn("current", provider.call_args.kwargs)
        history = json.loads(
            self.paths.evaluation_prompt_path("creative").read_text(encoding="utf-8")
        )["records"][-1]
        self.assertEqual(history["operation"], "regenerate")
        self.assertNotIn("current_output", history["input_fields"])
        self.assertNotIn("user_feedback", history["input_fields"])

    def test_09_revision_failures_keep_canonical_and_checkpoint_unchanged(self):
        from creative_workflow import regenerate_creative_stage, revise_creative_stage

        self.mark_waiting_review()
        canonical_before = self.paths.creative_brief_path().read_bytes()
        checkpoint_before = self.paths.project_state_path().read_bytes()

        with patch(
            "creative_workflow.revise_creative_brief",
            side_effect=RuntimeError("provider failed"),
        ):
            with self.assertRaises(RuntimeError):
                revise_creative_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    self.brief(),
                    "修改意见",
                    "mock-key",
                    self.logger,
                )
        self.assertEqual(self.paths.creative_brief_path().read_bytes(), canonical_before)
        self.assertEqual(self.paths.project_state_path().read_bytes(), checkpoint_before)

        with patch(
            "creative_workflow.generate_creative_brief",
            side_effect=RuntimeError("provider failed"),
        ):
            with self.assertRaises(RuntimeError):
                regenerate_creative_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    self.logger,
                )
        self.assertEqual(self.paths.creative_brief_path().read_bytes(), canonical_before)
        self.assertEqual(self.paths.project_state_path().read_bytes(), checkpoint_before)

    def test_10_revision_requires_waiting_review_and_nonempty_feedback(self):
        from creative_workflow import CreativeRevisionError, revise_creative_stage

        with self.assertRaises(CreativeRevisionError):
            revise_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.brief(),
                "修改",
                "mock-key",
                self.logger,
            )
        self.mark_waiting_review()
        with self.assertRaises(CreativeRevisionError):
            revise_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.brief(),
                "   ",
                "mock-key",
                self.logger,
            )

    def test_11_cli_revise_and_regenerate_callbacks_use_shared_core_callables(self):
        import main

        class SharedReached(RuntimeError):
            pass

        for callback_name, patched_name in (
            ("revise", "revise_creative_stage"),
            ("regenerate", "regenerate_creative_stage"),
        ):
            with self.subTest(callback=callback_name):
                self.mark_waiting_review()

                def invoke_callback(*args, **kwargs):
                    if callback_name == "revise":
                        kwargs[callback_name](args[3], "修改意见")
                    else:
                        kwargs[callback_name]()

                with (
                    patch.object(main, "human_review_gate", side_effect=invoke_callback),
                    patch.object(
                        main,
                        patched_name,
                        side_effect=SharedReached,
                    ) as shared,
                ):
                    with self.assertRaises(SharedReached):
                        main.run_pipeline(
                            self.paths,
                            self.request,
                            self.checkpoint,
                            "mock-key",
                            {},
                            self.logger,
                        )
                shared.assert_called_once()

    def test_12_cli_resume_loads_successfully_revised_canonical(self):
        import main
        from creative_workflow import revise_creative_stage

        class ReviewReached(RuntimeError):
            pass

        self.mark_waiting_review()
        replacement = self.brief().model_copy(
            update={"creative_concept": "Resume读取的新Creative"}
        )
        with patch(
            "creative_workflow.revise_creative_brief",
            return_value=replacement,
        ):
            revise_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                self.brief(),
                "修改意见",
                "mock-key",
                self.logger,
            )

        def inspect_gate(*args, **_kwargs):
            self.assertEqual(args[3].creative_concept, "Resume读取的新Creative")
            raise ReviewReached

        with patch.object(main, "human_review_gate", side_effect=inspect_gate):
            with self.assertRaises(ReviewReached):
                main.run_pipeline(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    {},
                    self.logger,
                )

    def mark_failed_initial_creative(self) -> None:
        from project_state import ProjectStage, StageStatus

        self.checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.RUNNING)
        self.checkpoint.fail(RuntimeError("mock initial Creative failure"))

    def test_13_failed_initial_creative_retries_through_shared_core_callable(self):
        from creative_workflow import retry_failed_creative_stage
        from project_state import ProjectStage, StageStatus

        self.mark_failed_initial_creative()
        with patch(
            "creative_workflow.generate_creative_brief",
            return_value=self.brief(),
        ) as provider:
            result = retry_failed_creative_stage(
                self.paths,
                self.request,
                self.checkpoint,
                "mock-key",
                self.logger,
            )
        provider.assert_called_once()
        self.assertEqual(result, self.brief())
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.CREATIVE),
            StageStatus.COMPLETED,
        )
        self.assertEqual(
            self.checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW),
            StageStatus.WAITING_REVIEW,
        )

    def test_14_failed_retry_rejects_canonical_without_mutating_checkpoint(self):
        from creative_workflow import CreativeRecoveryError, retry_failed_creative_stage

        self.mark_failed_initial_creative()
        self.paths.save_json(self.paths.creative_brief_path(), self.brief().model_dump())
        before = self.paths.project_state_path().read_bytes()
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with patch("creative_workflow.generate_creative_brief", provider):
            with self.assertRaises(CreativeRecoveryError):
                retry_failed_creative_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    self.logger,
                )
        provider.assert_not_called()
        self.assertEqual(self.paths.project_state_path().read_bytes(), before)

    def test_15_failed_retry_rejects_downstream_shot_artifact(self):
        from creative_workflow import CreativeRecoveryError, retry_failed_creative_stage

        self.mark_failed_initial_creative()
        shot_artifact = self.paths.shot_dir(1) / "unexpected.bin"
        shot_artifact.parent.mkdir(parents=True, exist_ok=True)
        shot_artifact.write_bytes(b"artifact")
        provider = Mock(side_effect=AssertionError("provider must not run"))
        with patch("creative_workflow.generate_creative_brief", provider):
            with self.assertRaises(CreativeRecoveryError):
                retry_failed_creative_stage(
                    self.paths,
                    self.request,
                    self.checkpoint,
                    "mock-key",
                    self.logger,
                )
        provider.assert_not_called()

    def test_16_cli_failed_resume_uses_the_shared_recovery_callable(self):
        import main

        class SharedRecoveryReached(RuntimeError):
            pass

        self.mark_failed_initial_creative()
        with patch.object(
            main,
            "retry_failed_creative_stage",
            side_effect=SharedRecoveryReached,
        ) as shared:
            with self.assertRaises(SharedRecoveryReached):
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
