from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
