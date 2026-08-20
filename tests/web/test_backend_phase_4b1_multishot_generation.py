from __future__ import annotations

import copy
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from unittest.mock import patch

from fastapi.testclient import TestClient

from project_manager import create_project_paths
from tests.web.test_backend_phase_1b_projects import base_project, write_json, write_project
from tests.web.web_response_assertions import assert_public_payload
from web_backend.models.tasks import TERMINAL_TASK_STATUSES, TaskResultReference
from video_provider import DownloadResult, ProviderTask, ProviderTaskStatus


class WebBackendPhase4B1MultiShotGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self._write_project("project-a", 3)
        self._write_project("project-b", 1)
        self.environment = patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "mock-deepseek-key",
                "MINIMAX_API_KEY": "mock-minimax-key",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real provider/network call"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=2,
            )
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    def _write_project(self, project_id: str, shot_count: int) -> Path:
        project = base_project(project_id=project_id, project_name="Multi Shot")
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            str(number): {
                "shot_id": number,
                "status": "NOT_STARTED",
                "generation_count": 0,
                "active_prompt_version": 1,
                "approved_prompt_version": 1,
                "active_video_version": None,
                "approved_video_version": None,
                "pending_video_version": None,
                "prompt_versions": [
                    {
                        "shot_id": number,
                        "version": 1,
                        "prompt": f"approved prompt {number}",
                        "source": "ai_generated",
                        "safety_prompt": f"safe prompt {number}",
                        "safety_is_safe": True,
                        "safety_risk_notes": [],
                        "safety_checked_at": "2026-08-20T00:00:00+00:00",
                    }
                ],
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            }
            for number in range(1, shot_count + 1)
        }
        directory = write_project(self.projects_root, project_id, project)
        storyboard = []
        prompts = []
        for number in range(1, shot_count + 1):
            storyboard.append(
                {
                    "shot_id": number,
                    "duration": 6,
                    "purpose": f"Shot purpose {number}",
                    "visual": f"Shot visual {number}",
                    "camera": "static",
                    "voiceover_cues": [],
                    "subtitle_cues": [],
                    "video_constraints": {
                        "reserve_subtitle_space": False,
                        "subtitle_safe_area": "none",
                    },
                }
            )
            prompts.append(
                {
                    "shot_id": number,
                    "visual_prompt_core": f"core {number}",
                    "video_prompt": f"approved prompt {number}",
                }
            )
        write_json(
            directory / "storyboard" / "storyboard.json",
            {"total_duration": shot_count * 6, "shots": storyboard},
        )
        write_json(
            directory / "storyboard" / "video_prompts.json",
            {"shots": prompts},
        )
        return directory

    def options(self, project_id: str = "project-a"):
        return self.client.get(
            f"/api/projects/{project_id}/shots/generation/options"
        )

    def start(self, shots: list[str], project_id: str = "project-a"):
        return self.client.post(
            f"/api/projects/{project_id}/shots/generation/start",
            json={"shots": shots, "confirm_paid_call": True},
        )

    def wait_terminal(self, task_ids: list[str], timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            records = [
                self.application.state.task_repository.get(task_id)
                for task_id in task_ids
            ]
            if all(record.status in TERMINAL_TASK_STATUSES for record in records):
                return records
            Event().wait(0.01)
        self.fail("tasks did not become terminal")

    @staticmethod
    def _result(shot_id: str) -> TaskResultReference:
        return TaskResultReference(
            resource_type="SHOT_VIDEO",
            resource_id=shot_id,
            version=1,
        )

    def test_01_options_are_backend_ordered_and_generation_ready(self):
        response = self.options()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["max_parallel"], 2)
        self.assertEqual(
            [shot["shot_id"] for shot in payload["shots"]],
            ["shot_01", "shot_02", "shot_03"],
        )
        self.assertTrue(all(shot["prompt_ready"] for shot in payload["shots"]))
        self.assertTrue(all(shot["available"] for shot in payload["shots"]))

    def test_02_plan_creates_one_existing_shot_task_per_selection(self):
        release = Event()
        entered = Event()
        count = 0
        guard = Lock()

        def run(_project, shot_id, *_args, **_kwargs):
            nonlocal count
            with guard:
                count += 1
                if count == 2:
                    entered.set()
            release.wait(2)
            return self._result(shot_id)

        with patch.object(
            self.application.state.shot_generation_action_service,
            "_run_start",
            side_effect=run,
        ):
            response = self.start(["shot_01", "shot_03"])
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(entered.wait(1))
            payload = response.json()
            self.assertEqual(
                [item["shot_id"] for item in payload["shots"]],
                ["shot_01", "shot_03"],
            )
            self.assertEqual(
                {item["operation"] for item in payload["shots"]},
                {"SHOT_GENERATE"},
            )
            self.assertEqual(len({item["task_id"] for item in payload["shots"]}), 2)
            self.assertNotIn("PROJECT_GENERATE_ALL", json.dumps(payload))
            release.set()
            self.wait_terminal([item["task_id"] for item in payload["shots"]])

    def test_03_executor_enforces_two_running_and_one_queued(self):
        release = Event()
        two_running = Event()
        running = 0
        guard = Lock()

        def run(_project, shot_id, *_args, **_kwargs):
            nonlocal running
            with guard:
                running += 1
                if running == 2:
                    two_running.set()
            release.wait(2)
            return self._result(shot_id)

        with patch.object(
            self.application.state.shot_generation_action_service,
            "_run_start",
            side_effect=run,
        ):
            response = self.start(["shot_01", "shot_02", "shot_03"])
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(two_running.wait(1))
            task_ids = [item["task_id"] for item in response.json()["shots"]]
            records = [self.application.state.task_repository.get(item) for item in task_ids]
            self.assertEqual(sum(record.status.value == "RUNNING" for record in records), 2)
            self.assertEqual(sum(record.status.value == "QUEUED" for record in records), 1)
            release.set()
            self.wait_terminal(task_ids)

    def test_04_different_projects_can_use_workers_independently(self):
        release = Event()
        both_running = Event()
        projects: set[str] = set()
        guard = Lock()

        def run(project_id, shot_id, *_args, **_kwargs):
            with guard:
                projects.add(project_id)
                if len(projects) == 2:
                    both_running.set()
            release.wait(2)
            return self._result(shot_id)

        with patch.object(
            self.application.state.shot_generation_action_service,
            "_run_start",
            side_effect=run,
        ):
            first = self.start(["shot_01"], "project-a")
            second = self.start(["shot_01"], "project-b")
            self.assertEqual((first.status_code, second.status_code), (202, 202))
            self.assertTrue(both_running.wait(1))
            release.set()
            self.wait_terminal(
                [first.json()["shots"][0]["task_id"], second.json()["shots"][0]["task_id"]]
            )

    def test_05_one_failure_does_not_fail_sibling_or_project(self):
        def run(_project, shot_id, *_args, **_kwargs):
            if shot_id == "shot_02":
                raise RuntimeError("safe synthetic failure")
            return None

        with patch.object(
            self.application.state.shot_generation_action_service,
            "_run_start",
            side_effect=run,
        ):
            response = self.start(["shot_01", "shot_02"])
            task_ids = [item["task_id"] for item in response.json()["shots"]]
            records = self.wait_terminal(task_ids)
        self.assertEqual(
            {record.target_id: record.status.value for record in records},
            {"shot_01": "SUCCEEDED", "shot_02": "FAILED"},
        )
        aggregation = self.options().json()
        self.assertEqual(aggregation["aggregation"]["failed"], 1)
        self.assertEqual(aggregation["status"], "PARTIAL_PROGRESS")
        project = self.client.get("/api/projects/project-a").json()
        self.assertNotEqual(project["workflow"]["status"], "FAILED")

    def test_06_refresh_recovers_active_tasks_without_resubmit(self):
        release = Event()
        entered = Event()
        calls = 0

        def run(_project, shot_id, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(2)
            return self._result(shot_id)

        with patch.object(
            self.application.state.shot_generation_action_service,
            "_run_start",
            side_effect=run,
        ):
            response = self.start(["shot_01"])
            task_id = response.json()["shots"][0]["task_id"]
            self.assertTrue(entered.wait(1))
            first = self.options().json()
            second = self.options().json()
            self.assertIn(first["shots"][0]["status"], {"QUEUED", "RUNNING"})
            self.assertEqual(first, second)
            self.assertEqual(calls, 1)
            self.assertEqual(
                len(self.application.state.task_repository.list_for_project("project-a")),
                1,
            )
            duplicate = self.start(["shot_01"])
            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(calls, 1)
            release.set()
            self.wait_terminal([task_id])

    def test_07_confirmation_and_invalid_selection_create_no_task(self):
        unconfirmed = self.client.post(
            "/api/projects/project-a/shots/generation/start",
            json={"shots": ["shot_01"], "confirm_paid_call": False},
        )
        missing = self.start(["shot_99"])
        self.assertEqual(unconfirmed.status_code, 422)
        self.assertEqual(missing.status_code, 409)
        self.assertFalse(self.runtime_root.exists())

    def test_08_public_dtos_contain_no_provider_locator_or_path(self):
        response = self.options()
        self.assertEqual(response.status_code, 200)
        assert_public_payload(self, response.json())
        rendered = json.dumps(response.json(), ensure_ascii=False).lower()
        for forbidden in (
            "provider_task_id",
            "file_id",
            "credential_env_name",
            "api_key",
            "video_path",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_09_shot_scoped_project_merge_preserves_sibling_updates(self):
        from web_backend.locking import ProjectLockManager
        from web_backend.services.shot_generation import _ShotScopedProjectPaths

        directory = self.projects_root / "project-a"
        target = directory / "project.json"
        baseline = json.loads(target.read_text(encoding="utf-8"))
        first = copy.deepcopy(baseline)
        second = copy.deepcopy(baseline)
        first["video_generation"]["shots"]["1"]["status"] = "GENERATING"
        second["video_generation"]["shots"]["2"]["status"] = "FAILED"
        paths = create_project_paths(directory)
        manager = ProjectLockManager()
        first_paths = _ShotScopedProjectPaths(paths, "project-a", 1, manager)
        second_paths = _ShotScopedProjectPaths(paths, "project-a", 2, manager)
        with ThreadPoolExecutor(max_workers=2) as executor:
            writes = [
                executor.submit(first_paths.save_json, target, first),
                executor.submit(second_paths.save_json, target, second),
            ]
            for write in writes:
                write.result(timeout=2)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["video_generation"]["shots"]["1"]["status"], "GENERATING")
        self.assertEqual(saved["video_generation"]["shots"]["2"]["status"], "FAILED")
        self.assertEqual(saved["video_generation"]["shots"]["3"]["status"], "NOT_STARTED")

    def test_10_task_operation_enum_has_no_project_generation_operation(self):
        from web_backend.models.tasks import TaskOperation

        self.assertNotIn("PROJECT_GENERATE_ALL", {item.value for item in TaskOperation})

    def test_11_real_core_pipeline_keeps_two_shot_bundles_and_pointers_isolated(self):
        from providers.minimax_hailuo_provider import MiniMaxHailuoProvider

        submitted: list[int] = []
        guard = Lock()

        def submit(adapter, request, task_logger=None):
            del task_logger
            with guard:
                submitted.append(int(request.shot_id or 0))
            return ProviderTask(
                adapter.provider_name,
                adapter.model_name,
                adapter.api_version,
                adapter.generation_mode(request.required_capability),
                f"provider-task-{request.shot_id}",
            )

        def poll(adapter, task, task_logger=None):
            del adapter, task_logger
            return task.evolve(
                status=ProviderTaskStatus.COMPLETED,
                provider_file_id=f"provider-file-{task.provider_task_id.rsplit('-', 1)[-1]}",
            )

        def download(adapter, task, output_path, request, task_logger=None):
            del adapter, task, request, task_logger
            payload = b"mock-mp4"
            output_path.write_bytes(payload)
            return DownloadResult(output_path, len(payload))

        with (
            patch.object(MiniMaxHailuoProvider, "submit", autospec=True, side_effect=submit),
            patch.object(MiniMaxHailuoProvider, "poll", autospec=True, side_effect=poll),
            patch.object(MiniMaxHailuoProvider, "download", autospec=True, side_effect=download),
        ):
            response = self.start(["shot_01", "shot_02"])
            self.assertEqual(response.status_code, 202, response.text)
            task_ids = [item["task_id"] for item in response.json()["shots"]]
            records = self.wait_terminal(task_ids, timeout=5)

        self.assertTrue(all(record.status.value == "SUCCEEDED" for record in records))
        self.assertEqual(sorted(submitted), [1, 2])
        project_dir = self.projects_root / "project-a"
        saved = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        for number in (1, 2):
            shot = saved["video_generation"]["shots"][str(number)]
            self.assertEqual(shot["status"], "WAITING_REVIEW")
            self.assertEqual(shot["generation_count"], 1)
            self.assertEqual(shot["active_video_version"], 1)
            bundle = project_dir / "shots" / f"shot_{number:02d}" / "v001"
            self.assertEqual(
                {item.name for item in bundle.iterdir()},
                {"video.mp4", "prompt.json", "safety.json", "generation.json", "review.json"},
            )
        self.assertEqual(
            saved["video_generation"]["shots"]["3"]["status"],
            "NOT_STARTED",
        )

    def test_12_full_plan_is_revalidated_before_any_task_is_created(self):
        from web_backend.models.generation import (
            GenerationIntent,
            GenerationPreflightRequest,
            GenerationStartRequest,
            GenerationVisualInputMode,
            GenerationVisualInputRequest,
            ModelSelectionMode,
        )
        from web_backend.services.shot_generation import GenerationPreflightStale

        preflight_payload = GenerationPreflightRequest(
            intent=GenerationIntent.INITIAL,
            model_selection=ModelSelectionMode.AUTO,
            visual_input=GenerationVisualInputRequest(
                mode=GenerationVisualInputMode.NONE,
                asset_ids=[],
            ),
        )
        preflight_service = self.application.state.shot_generation_preflight_service
        first = preflight_service.preflight("project-a", "shot_01", preflight_payload)
        second = preflight_service.preflight("project-a", "shot_02", preflight_payload)
        valid = GenerationStartRequest(
            **preflight_payload.model_dump(),
            preflight_fingerprint=first.preflight_fingerprint,
            confirm_paid_call=True,
        )
        stale = GenerationStartRequest(
            **preflight_payload.model_dump(),
            preflight_fingerprint=("0" * 64 if second.preflight_fingerprint != "0" * 64 else "1" * 64),
            confirm_paid_call=True,
        )
        with self.assertRaises(GenerationPreflightStale):
            self.application.state.shot_generation_action_service.submit_batch_starts(
                "project-a",
                [("shot_01", valid), ("shot_02", stale)],
                correlation_id="req_atomic_plan",
            )
        self.assertFalse(self.runtime_root.exists())

    def test_13_storyboard_only_legacy_shot_stays_visible_but_unavailable(self):
        target = self.projects_root / "project-a" / "project.json"
        project = json.loads(target.read_text(encoding="utf-8"))
        project["video_generation"]["shots"].pop("3")
        write_json(target, project)

        response = self.options()

        self.assertEqual(response.status_code, 200, response.text)
        shots = response.json()["shots"]
        self.assertEqual([shot["shot_id"] for shot in shots], ["shot_01", "shot_02", "shot_03"])
        self.assertFalse(shots[2]["available"])
        self.assertTrue(shots[2]["prompt_ready"])


if __name__ == "__main__":
    unittest.main()
