from __future__ import annotations

import copy
import json
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import tests.web.test_backend_phase_3d2_shot_generation as phase3d2
import tests.web.test_backend_phase_3d3c_manual_prompt_regeneration as phase3d3c
import tests.web.test_backend_phase_3d3cb2a_prompt_revision_adopt as phase3d3cb2a
import tests.web.test_backend_phase_3d3cb2b_prompt_version_generation as phase3d3cb2b
from project_manager import ProjectDirectoryError, ProjectPaths
from prompt_generator import PromptSafetyReview
from shot_generation_workflow import (
    generate_initial_shot,
    regenerate_shot_with_current_prompt,
    regenerate_shot_with_prompt_version,
    resume_shot_generation,
)
from tests.test_shot_generation_workflow import FakeCoreVideoGenerator
from tests.web.test_backend_phase_1b_projects import tree_snapshot
from video_provider import ProviderErrorCode, VideoProviderError
from web_backend.models.tasks import (
    TaskOperation,
    TaskRecord,
    TaskResultReference,
    TaskStatus,
)


class WebBackendPhase3D3CDGenerationReliabilityTests(unittest.TestCase):
    setUp = phase3d2.WebBackendPhase3D2ShotGenerationTests.setUp
    _write_project = phase3d2.WebBackendPhase3D2ShotGenerationTests._write_project
    _write_reference = staticmethod(
        phase3d2.WebBackendPhase3D2ShotGenerationTests._write_reference
    )
    payload = staticmethod(phase3d2.WebBackendPhase3D2ShotGenerationTests.payload)
    preflight = phase3d2.WebBackendPhase3D2ShotGenerationTests.preflight
    start = phase3d2.WebBackendPhase3D2ShotGenerationTests.start
    wait_terminal = phase3d2.WebBackendPhase3D2ShotGenerationTests.wait_terminal
    provider_patches = phase3d2.WebBackendPhase3D2ShotGenerationTests.provider_patches
    _core = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._core
    _generate_v1 = phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._generate_v1
    _generate_and_approve_v1 = (
        phase3d3c.WebBackendPhase3D3CManualPromptRegenerationTests._generate_and_approve_v1
    )
    _prepare_adopted_prompt = (
        phase3d3cb2b.WebBackendPhase3D3CB2BPromptVersionGenerationTests._prepare_adopted_prompt
    )

    @staticmethod
    def _running_task(operation: TaskOperation, marker: str) -> TaskRecord:
        now = datetime.now(timezone.utc)
        return TaskRecord(
            task_id=f"task_{marker * 32}",
            project_id="project-a",
            operation=operation,
            target_id="shot_01",
            status=TaskStatus.RUNNING,
            created_at=now,
            started_at=now,
            correlation_id=f"req_crash_{marker}",
        )

    def _restart_app(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        return create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            )
        )

    def _mark_running(self, task: TaskRecord) -> TaskRecord:
        payload = task.model_dump()
        payload.update(
            status=TaskStatus.RUNNING,
            started_at=task.started_at or datetime.now(timezone.utc),
            finished_at=None,
            error=None,
            result=None,
        )
        return self.application.state.task_repository.update(
            TaskRecord.model_validate(payload)
        )

    def test_01_restart_interrupts_paid_tasks_without_replay_or_version_allocation(self):
        operations = (
            TaskOperation.SHOT_GENERATE,
            TaskOperation.SHOT_REGENERATE,
            TaskOperation.SHOT_PROMPT_VERSION_GENERATE,
        )
        before = (self.project_dir / "project.json").read_bytes()
        records = [
            self.application.state.task_repository.create(
                self._running_task(operation, str(index))
            )
            for index, operation in enumerate(operations, start=1)
        ]
        restarted = self._restart_app()
        with patch.object(
            restarted.state.task_runner,
            "submit",
            side_effect=AssertionError("crash recovery replayed a task"),
        ) as submit:
            with TestClient(restarted):
                recovered = [
                    restarted.state.task_repository.get(record.task_id)
                    for record in records
                ]
        submit.assert_not_called()
        self.assertEqual(
            [item.status.value for item in recovered],
            ["INTERRUPTED", "INTERRUPTED", "INTERRUPTED"],
        )
        self.assertTrue(all(item.error.code == "TASK_INTERRUPTED" for item in recovered))
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        project = json.loads(before)
        entry = project["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 0)
        self.assertEqual(entry["generation_versions"], [])

    def test_02_restart_preserves_provider_progress_and_offers_resume_without_submit(self):
        failure = VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            "mock poll interruption",
            retryable=True,
        )
        with self.provider_patches(poll_error=failure):
            accepted = self.start()
            failed = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(self.submit_calls, 1)
        self._mark_running(failed)
        restarted = self._restart_app()
        with patch.object(restarted.state.task_runner, "submit") as submit:
            with TestClient(restarted) as client:
                status = client.get(
                    "/api/projects/project-a/shots/shot_01/generation/status"
                ).json()
                recovered = restarted.state.task_repository.get(failed.task_id)
        submit.assert_not_called()
        self.assertEqual(recovered.status, TaskStatus.INTERRUPTED)
        self.assertEqual(status["resume_kind"], "POLL_EXISTING_TASK")
        self.assertTrue(status["resume_available"])
        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 1)
        self.assertEqual(len(entry["generation_versions"]), 1)

    def test_03_provider_success_download_recovery_never_resubmits_or_repolls(self):
        failure = VideoProviderError(
            ProviderErrorCode.DOWNLOAD_FAILED,
            "mock download interruption",
            retryable=True,
        )
        with self.provider_patches(download_error=failure):
            accepted = self.start()
            self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), (1, 1, 1))
        with self.provider_patches():
            resumed = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/resume"
            )
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status, TaskStatus.SUCCEEDED)
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), (1, 1, 2))
        self.assertEqual(terminal.result.version, 1)

    def test_04_local_finalize_recovery_uses_no_provider_method(self):
        failure = OSError("mock local finalize interruption")
        with self.provider_patches(
            download_error=failure,
            write_before_download_error=True,
        ):
            accepted = self.start()
            self.wait_terminal(accepted.json()["task_id"])
        before = (self.submit_calls, self.poll_calls, self.download_calls)
        from providers.minimax_hailuo_provider import MiniMaxHailuoProvider

        with (
            patch.object(MiniMaxHailuoProvider, "submit", side_effect=AssertionError("submit")),
            patch.object(MiniMaxHailuoProvider, "poll", side_effect=AssertionError("poll")),
            patch.object(MiniMaxHailuoProvider, "download", side_effect=AssertionError("download")),
        ):
            resumed = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/resume"
            )
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status, TaskStatus.SUCCEEDED)
        self.assertEqual((self.submit_calls, self.poll_calls, self.download_calls), before)
        self.assertTrue(
            (self.project_dir / "shots" / "shot_01" / "v001" / "video.mp4").is_file()
        )

    def test_05_submission_unknown_is_manual_only_and_never_retried(self):
        ambiguous = VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            "mock ambiguous submit",
            retryable=True,
        )
        with self.provider_patches(submit_error=ambiguous):
            accepted = self.start()
            failed = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(failed.error.code, "SUBMISSION_UNKNOWN")
        before_tasks = len(
            self.application.state.task_repository.list_for_project("project-a")
        )
        resume = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/resume"
        )
        self.assertEqual(resume.status_code, 409)
        self.assertEqual(self.submit_calls, 1)
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            before_tasks,
        )
        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(entry["generation_count"], 1)

    def test_06_regeneration_crash_resume_preserves_official_and_prompt_binding(self):
        self._generate_and_approve_v1()
        official = self.project_dir / "shots" / "shot_01" / "v001"
        official_before = tree_snapshot(official)
        payload = {
            "intent": "REGENERATE_CURRENT_PROMPT",
            "model_selection": "AUTO",
            "requested_model": None,
            "visual_input": {"mode": "none", "asset_ids": []},
        }
        checked = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=payload,
        ).json()
        fake = FakeCoreVideoGenerator(fail_after_submit=True)

        def interrupted(**kwargs):
            return regenerate_shot_with_current_prompt(
                **kwargs,
                video_generate=fake,
                safety_review=lambda *_args, **_kwargs: self.fail(
                    "approved Prompt safety should be reused"
                ),
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_current_prompt",
            side_effect=interrupted,
        ):
            accepted = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/regenerate",
                json={
                    **payload,
                    "preflight_fingerprint": checked["preflight_fingerprint"],
                    "confirm_paid_call": True,
                },
            )
            failed = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(fake.submit_calls, 1)
        interrupted_entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(interrupted_entry["approved_video_version"], 1)
        self.assertEqual(interrupted_entry["approved_prompt_version"], 2)
        self.assertEqual(interrupted_entry["candidate"]["prompt_version"], 2)
        self.assertEqual(interrupted_entry["generation_count"], 2)
        self.assertEqual(tree_snapshot(official), official_before)

        fake.fail_after_submit = False

        def resume(**kwargs):
            return resume_shot_generation(**kwargs, video_generate=fake)

        with patch(
            "web_backend.services.shot_generation.resume_shot_generation",
            side_effect=resume,
        ):
            response = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/resume"
            )
            terminal = self.wait_terminal(response.json()["task_id"])
        self.assertEqual(terminal.status, TaskStatus.SUCCEEDED)
        self.assertEqual(terminal.result.version, 2)
        self.assertEqual(fake.submit_calls, 1)
        bundle = self.project_dir / "shots" / "shot_01" / "v002"
        self.assertEqual(
            json.loads((bundle / "prompt.json").read_text(encoding="utf-8"))[
                "prompt_version"
            ],
            2,
        )
        final_entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(final_entry["approved_video_version"], 1)
        self.assertEqual(final_entry["approved_prompt_version"], 2)
        self.assertEqual(final_entry["generation_count"], 2)

    def test_07_selected_prompt_crash_resume_keeps_prompt_three_binding(self):
        self._prepare_adopted_prompt()
        payload = phase3d3cb2b.WebBackendPhase3D3CB2BPromptVersionGenerationTests._payload()
        checked = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/preflight",
            json=payload,
        ).json()
        fake = FakeCoreVideoGenerator(fail_after_submit=True)
        safety = Mock(
            return_value=PromptSafetyReview(
                is_safe=True,
                risk_notes=[],
                reviewed_video_prompt="safe adopted prompt",
            )
        )

        def interrupted(**kwargs):
            return regenerate_shot_with_prompt_version(
                **kwargs,
                video_generate=fake,
                safety_review=safety,
            )

        with patch(
            "web_backend.services.shot_generation.regenerate_shot_with_prompt_version",
            side_effect=interrupted,
        ):
            accepted = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/prompt-version",
                json={
                    **payload,
                    "preflight_fingerprint": checked["preflight_fingerprint"],
                    "confirm_paid_call": True,
                },
            )
            self.wait_terminal(accepted.json()["task_id"])
        fake.fail_after_submit = False

        with patch(
            "web_backend.services.shot_generation.resume_shot_generation",
            side_effect=lambda **kwargs: resume_shot_generation(
                **kwargs,
                video_generate=fake,
            ),
        ):
            resumed = self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/resume"
            )
            terminal = self.wait_terminal(resumed.json()["task_id"])
        self.assertEqual(terminal.status, TaskStatus.SUCCEEDED)
        self.assertEqual(fake.submit_calls, 1)
        bundle = self.project_dir / "shots" / "shot_01" / "v002"
        prompt = json.loads((bundle / "prompt.json").read_text(encoding="utf-8"))
        generation = json.loads(
            (bundle / "generation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(prompt["prompt_version"], 3)
        self.assertEqual(generation["prompt_version"], 3)
        entry = json.loads(
            (self.project_dir / "project.json").read_text(encoding="utf-8")
        )["video_generation"]["shots"]["1"]
        self.assertEqual(entry["approved_video_version"], 1)
        self.assertEqual(entry["approved_prompt_version"], 2)
        self.assertEqual(entry["candidate"]["prompt_version"], 3)

    def test_08_completed_bundle_rejects_resume_without_task_or_version(self):
        with self.provider_patches():
            accepted = self.start()
            self.wait_terminal(accepted.json()["task_id"])
        before_tasks = len(
            self.application.state.task_repository.list_for_project("project-a")
        )
        before = (self.project_dir / "project.json").read_bytes()
        response = self.client.post(
            "/api/projects/project-a/shots/shot_01/generation/resume"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("project-a")),
            before_tasks,
        )
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)
        self.assertEqual(self.submit_calls, 1)

    def test_09_failed_task_with_complete_bundle_uses_business_state(self):
        fake = FakeCoreVideoGenerator()

        def complete_then_fail(**kwargs):
            generate_initial_shot(**kwargs, video_generate=fake)
            raise OSError("mock crash after canonical finalize")

        with patch(
            "web_backend.services.shot_generation.generate_initial_shot",
            side_effect=complete_then_fail,
        ):
            accepted = self.start()
            failed = self.wait_terminal(accepted.json()["task_id"])
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.error.code, "SHOT_GENERATION_FAILED")
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        ).json()
        self.assertEqual(status["state"], "WAITING_REVIEW")
        self.assertFalse(status["resume_available"])
        shot = self.client.get("/api/projects/project-a/shots/shot_01").json()
        self.assertEqual(shot["pending_review_version"], 1)
        self.assertEqual(fake.submit_calls, 1)

    def test_10_succeeded_task_without_bundle_never_fabricates_business_success(self):
        now = datetime.now(timezone.utc)
        task = self.application.state.task_repository.create(
            TaskRecord(
                task_id=f"task_{'a' * 32}",
                project_id="project-a",
                operation=TaskOperation.SHOT_GENERATE,
                target_id="shot_01",
                status=TaskStatus.SUCCEEDED,
                created_at=now,
                started_at=now,
                finished_at=now,
                correlation_id="req_forged_success",
                result=TaskResultReference(
                    resource_type="SHOT_VIDEO",
                    resource_id="shot_01",
                    version=1,
                ),
            )
        )
        status = self.client.get(
            "/api/projects/project-a/shots/shot_01/generation/status"
        ).json()
        self.assertEqual(status["state"], "NOT_STARTED")
        self.assertIsNone(status["video_version"])
        self.assertFalse(
            (self.project_dir / "shots" / "shot_01" / "v001" / "video.mp4").exists()
        )
        public_task = self.client.get(f"/api/tasks/{task.task_id}").json()
        rendered = json.dumps(public_task, ensure_ascii=False).lower()
        for forbidden in ("provider_task_id", "file_id", "credential", "d:\\"):
            self.assertNotIn(forbidden, rendered)


class WebBackendPhase3D3CDPromptReliabilityTests(unittest.TestCase):
    setUp = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests.setUp
    _write_project = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._write_project
    _write_reference = staticmethod(
        phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._write_reference
    )
    wait_terminal = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests.wait_terminal
    _prepare = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._prepare
    _result = staticmethod(phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._result)
    _prepare_adoptable = (
        phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._prepare_adoptable
    )
    _create_draft = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._create_draft
    _adopt = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._adopt
    _draft_path = phase3d3cb2a.WebBackendPhase3D3CB2APromptRevisionAdoptTests._draft_path

    def _restart_app(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        return create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            )
        )

    def _mark_running(self, task: TaskRecord) -> None:
        payload = task.model_dump()
        payload.update(
            status=TaskStatus.RUNNING,
            started_at=task.started_at or datetime.now(timezone.utc),
            finished_at=None,
            error=None,
            result=None,
        )
        self.application.state.task_repository.update(
            TaskRecord.model_validate(payload)
        )

    def test_11_persisted_draft_survives_task_crash_without_deepseek_replay(self):
        self._prepare_adoptable()
        self._create_draft()
        task = self.application.state.task_repository.list_for_project("project-a")[0]
        self._mark_running(task)
        before_project = (self.project_dir / "project.json").read_bytes()
        restarted = self._restart_app()
        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=AssertionError("DeepSeek replayed during restart"),
        ) as provider:
            with TestClient(restarted) as client:
                response = client.get(
                    "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
                )
                recovered = restarted.state.task_repository.get(task.task_id)
        provider.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["draft_prompt"])
        self.assertEqual(recovered.status, TaskStatus.INTERRUPTED)
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before_project)

    def test_12_missing_draft_crash_is_interrupted_without_prompt_or_provider(self):
        self._prepare_adoptable()
        before = (self.project_dir / "project.json").read_bytes()
        now = datetime.now(timezone.utc)
        running = self.application.state.task_repository.create(
            TaskRecord(
                task_id=f"task_{'b' * 32}",
                project_id="project-a",
                operation=TaskOperation.SHOT_PROMPT_REVISION_DRAFT,
                target_id="shot_01",
                status=TaskStatus.RUNNING,
                created_at=now,
                started_at=now,
                correlation_id="req_draft_crash",
            )
        )
        restarted = self._restart_app()
        with patch(
            "web_backend.services.prompt_revision.generate_prompt_revision_draft",
            side_effect=AssertionError("DeepSeek replayed during restart"),
        ) as provider:
            with TestClient(restarted) as client:
                response = client.get(
                    "/api/projects/project-a/shots/shot_01/prompt/revision/draft"
                )
                recovered = restarted.state.task_repository.get(running.task_id)
        provider.assert_not_called()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(recovered.status, TaskStatus.INTERRUPTED)
        self.assertEqual((self.project_dir / "project.json").read_bytes(), before)

    def test_13_adopt_write_failure_rolls_back_both_files_and_retry_is_single_version(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        plan_path = self.project_dir / "storyboard" / "video_prompts.json"
        project_before = json.loads(project_path.read_text(encoding="utf-8"))
        plan_before = json.loads(plan_path.read_text(encoding="utf-8"))
        original_save = ProjectPaths.save_json
        failed_once = False

        def fail_checkpoint_once(paths, path, data):
            nonlocal failed_once
            if Path(path).name == "project.json" and not failed_once:
                failed_once = True
                raise ProjectDirectoryError("mock checkpoint write interruption")
            return original_save(paths, path, data)

        with patch.object(
            ProjectPaths,
            "save_json",
            autospec=True,
            side_effect=fail_checkpoint_once,
        ):
            failed = self._adopt()
        self.assertEqual(failed.status_code, 422, failed.text)
        self.assertEqual(failed.json()["error"]["code"], "PROJECT_DATA_CORRUPT")
        self.assertEqual(json.loads(project_path.read_text(encoding="utf-8")), project_before)
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8")), plan_before)

        adopted = self._adopt()
        self.assertEqual(adopted.status_code, 200, adopted.text)
        final = json.loads(project_path.read_text(encoding="utf-8"))
        versions = final["video_generation"]["shots"]["1"]["prompt_versions"]
        self.assertEqual([item["version"] for item in versions], [2, 3])
        self.assertEqual(final["video_generation"]["shots"]["1"]["active_prompt_version"], 3)

    def test_14_atomic_replace_failure_keeps_valid_old_or_new_prompt_state(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        plan_path = self.project_dir / "storyboard" / "video_prompts.json"
        project_before = copy.deepcopy(json.loads(project_path.read_text(encoding="utf-8")))
        plan_before = copy.deepcopy(json.loads(plan_path.read_text(encoding="utf-8")))
        original_replace = os.replace
        failed_once = False

        def fail_plan_replace_once(source, target):
            nonlocal failed_once
            if Path(target).name == "video_prompts.json" and not failed_once:
                failed_once = True
                error = PermissionError(13, "mock non-retryable replace failure")
                error.winerror = 2
                raise error
            return original_replace(source, target)

        with patch("project_manager.os.replace", side_effect=fail_plan_replace_once):
            failed = self._adopt()
        self.assertEqual(failed.status_code, 422, failed.text)
        self.assertEqual(json.loads(project_path.read_text(encoding="utf-8")), project_before)
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8")), plan_before)
        self.assertEqual(list(plan_path.parent.glob(".video_prompts.json.*.tmp")), [])
        self.assertEqual(list(project_path.parent.glob(".project.json.*.tmp")), [])

    def test_15_task_contract_and_openapi_remain_public_safe(self):
        schema = self.client.get("/openapi.json").json()
        schemas = schema["components"]["schemas"]
        backend_operations = [item.value for item in TaskOperation]
        self.assertEqual(schemas["TaskOperation"]["enum"], backend_operations)
        self.assertEqual(
            schemas["TaskStatus"]["enum"],
            [item.value for item in TaskStatus],
        )
        frontend_types = (
            Path(__file__).resolve().parents[2]
            / "frontend"
            / "src"
            / "api"
            / "types.ts"
        ).read_text(encoding="utf-8")
        operation_block = re.search(
            r"export const TASK_OPERATIONS = \[(?P<body>.*?)\] as const",
            frontend_types,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(operation_block)
        self.assertEqual(
            re.findall(r'"([A-Z][A-Z0-9_]*)"', operation_block.group("body")),
            backend_operations,
        )
        rendered = json.dumps(schema, ensure_ascii=False).lower()
        for forbidden in (
            "provider_task_id",
            "file_id",
            "api_key",
            "credential_env_name",
            "absolute_path",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_16_adopt_failure_preserves_video_generation_and_official_pointers(self):
        self._prepare_adoptable()
        self._create_draft()
        project_path = self.project_dir / "project.json"
        before = json.loads(project_path.read_text(encoding="utf-8"))
        entry_before = copy.deepcopy(before["video_generation"]["shots"]["1"])
        original_save = ProjectPaths.save_json
        failed_once = False

        def fail_checkpoint_once(paths, path, data):
            nonlocal failed_once
            if Path(path).name == "project.json" and not failed_once:
                failed_once = True
                raise ProjectDirectoryError("mock adopt interruption")
            return original_save(paths, path, data)

        with patch.object(
            ProjectPaths,
            "save_json",
            autospec=True,
            side_effect=fail_checkpoint_once,
        ):
            response = self._adopt()
        self.assertEqual(response.status_code, 422)
        entry_after = json.loads(project_path.read_text(encoding="utf-8"))[
            "video_generation"
        ]["shots"]["1"]
        self.assertEqual(entry_after, entry_before)
        self.assertEqual(entry_after["approved_video_version"], 1)
        self.assertEqual(entry_after["approved_prompt_version"], 2)
        self.assertEqual(entry_after["generation_count"], 1)


if __name__ == "__main__":
    unittest.main()
