from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload


class RecordingProjectLockManager:
    def __init__(self) -> None:
        from web_backend.locking import ProjectLockManager

        self.delegate = ProjectLockManager()
        self.acquired: list[str] = []

    @contextmanager
    def project_write(self, project_id: str, *, timeout_seconds: float = 0.0):
        self.acquired.append(project_id)
        with self.delegate.project_write(
            project_id,
            timeout_seconds=timeout_seconds,
        ):
            yield

    def project_creation(self, *args, **kwargs):
        return self.delegate.project_creation(*args, **kwargs)


class WebBackendPhase3A1TaskTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.repositories.project_repository import ProjectRepository
        from web_backend.repositories.task_repository import TaskRepository
        from web_backend.services.task_runner import TaskRunner
        from web_backend.services.tasks import TaskService

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.projects_root / ".web_runtime"
        self.project_a = write_project(
            self.projects_root,
            "project-a",
            base_project(project_id="project-a"),
        )
        self.project_b = write_project(
            self.projects_root,
            "project-b",
            base_project(project_id="project-b"),
        )
        self.project_repository = ProjectRepository(self.projects_root)
        self.task_repository = TaskRepository(self.runtime_root)
        self.lock_manager = RecordingProjectLockManager()
        self.runner = TaskRunner(
            self.task_repository,
            self.lock_manager,
            max_workers=2,
        )
        self.service = TaskService(
            self.task_repository,
            self.runner,
            self.project_repository,
        )
        self.addCleanup(self.runner.shutdown)

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    def record_for(self, *, status=None, project_id="project-a"):
        from web_backend.models.tasks import TaskError, TaskOperation, TaskRecord, TaskStatus

        status = status or TaskStatus.QUEUED
        started_at = self.now() if status is TaskStatus.RUNNING else None
        finished_at = (
            self.now()
            if status
            in {
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.INTERRUPTED,
                TaskStatus.CANCELLED,
            }
            else None
        )
        error = None
        if status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            error = TaskError(
                code="TASK_EXECUTION_FAILED",
                message="任务执行失败。",
                retryable=False,
            )
        return TaskRecord(
            task_id=f"task_{uuid.uuid4().hex}",
            project_id=project_id,
            operation=TaskOperation.CREATIVE_GENERATE,
            status=status,
            created_at=self.now(),
            started_at=started_at,
            finished_at=finished_at,
            correlation_id="req_phase3a1",
            error=error,
        )

    def submit(self, callable_=None, *, project_id="project-a"):
        from web_backend.models.tasks import TaskOperation

        return self.service.submit(
            project_id=project_id,
            operation=TaskOperation.CREATIVE_GENERATE,
            correlation_id="req_phase3a1",
            callable_=callable_ or (lambda: None),
        )

    def wait_for_status(self, task_id, expected, *, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.task_repository.get(task_id)
            if record.status is expected:
                return record
            Event().wait(0.005)
        self.fail(f"task {task_id} did not reach {expected}")

    def client_for(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=2,
            ),
            lock_manager=self.lock_manager,
        )
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(application.state.task_runner.shutdown)
        self.addCleanup(client.close)
        return application, client

    def test_01_create_task_record(self):
        record = self.record_for()
        self.assertEqual(self.task_repository.create(record), record)
        self.assertEqual(self.task_repository.get(record.task_id), record)

    def test_02_task_id_is_random_url_safe_uuid_hex(self):
        first = self.submit()
        second = self.submit(project_id="project-b")
        self.assertRegex(first.task_id, r"^task_[0-9a-f]{32}$")
        self.assertNotEqual(first.task_id, second.task_id)

    def test_03_new_record_is_queued(self):
        record = self.task_repository.create(self.record_for())
        self.assertEqual(record.status.value, "QUEUED")
        self.assertIsNone(record.started_at)
        self.assertIsNone(record.finished_at)

    def test_04_runner_records_running_before_callable(self):
        entered = Event()
        release = Event()

        def controlled():
            entered.set()
            release.wait(timeout=2)

        task = self.submit(controlled)
        self.assertTrue(entered.wait(timeout=1))
        running = self.task_repository.get(task.task_id)
        self.assertEqual(running.status.value, "RUNNING")
        self.assertIsNotNone(running.started_at)
        release.set()

    def test_05_runner_records_succeeded_and_result_reference(self):
        from web_backend.models.tasks import TaskResultReference, TaskStatus

        task = self.submit(
            lambda: TaskResultReference(
                resource_type="PROJECT",
                resource_id="project-a",
                version=1,
            )
        )
        succeeded = self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        self.assertEqual(succeeded.result.resource_id, "project-a")
        self.assertIsNotNone(succeeded.finished_at)

    def test_06_unknown_failure_becomes_failed(self):
        from web_backend.models.tasks import TaskStatus

        def fail():
            raise RuntimeError("simulated")

        task = self.submit(fail)
        failed = self.wait_for_status(task.task_id, TaskStatus.FAILED)
        self.assertEqual(failed.error.code, "TASK_EXECUTION_FAILED")

    def test_07_explicit_safe_task_error_is_preserved(self):
        from web_backend.models.tasks import TaskError, TaskStatus
        from web_backend.services.task_runner import TaskExecutionFailure

        def fail_safely():
            raise TaskExecutionFailure(
                TaskError(
                    code="SAFE_BUSINESS_ERROR",
                    message="业务操作无法完成。",
                    retryable=True,
                )
            )

        task = self.submit(fail_safely)
        failed = self.wait_for_status(task.task_id, TaskStatus.FAILED)
        self.assertEqual(failed.error.code, "SAFE_BUSINESS_ERROR")
        self.assertTrue(failed.error.retryable)

    def test_08_raw_exception_is_not_persisted(self):
        from web_backend.models.tasks import TaskStatus

        def fail():
            raise RuntimeError(r"D:\private API_KEY=secret provider raw response")

        task = self.submit(fail)
        self.wait_for_status(task.task_id, TaskStatus.FAILED)
        rendered = (self.runtime_root / "tasks" / f"{task.task_id}.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("D:\\private", rendered)
        self.assertNotIn("API_KEY", rendered)
        self.assertNotIn("provider raw", rendered)

    def test_09_atomic_update_keeps_original_when_replace_fails(self):
        from web_backend.models.tasks import TaskRecord, TaskStatus

        queued = self.task_repository.create(self.record_for())
        payload = queued.model_dump()
        payload.update(status=TaskStatus.RUNNING, started_at=self.now())
        running = TaskRecord.model_validate(payload)
        with patch(
            "web_backend.repositories.task_repository.os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with self.assertRaises(OSError):
                self.task_repository.update(running)
        self.assertEqual(self.task_repository.get(queued.task_id).status, TaskStatus.QUEUED)
        self.assertEqual(list((self.runtime_root / "tasks").glob("*.tmp")), [])

    def test_10_task_get_returns_safe_record(self):
        record = self.task_repository.create(self.record_for())
        _, client = self.client_for()
        response = client.get(f"/api/tasks/{record.task_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], record.task_id)
        assert_public_payload(self, response.json())

    def test_11_task_get_not_found_is_safe_404(self):
        _, client = self.client_for()
        response = client.get(f"/api/tasks/task_{'0' * 32}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "TASK_NOT_FOUND")

    def test_12_invalid_task_id_is_422(self):
        _, client = self.client_for()
        response = client.get("/api/tasks/not-a-task")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_TASK_ID")

    def test_13_project_task_list_is_newest_first(self):
        older = self.record_for()
        newer = self.record_for()
        newer_payload = newer.model_dump()
        newer_payload["created_at"] = datetime(2030, 1, 1, tzinfo=timezone.utc)
        from web_backend.models.tasks import TaskRecord

        newer = TaskRecord.model_validate(newer_payload)
        self.task_repository.create(older)
        self.task_repository.create(newer)
        _, client = self.client_for()
        response = client.get("/api/projects/project-a/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["task_id"] for item in response.json()["tasks"]],
            [newer.task_id, older.task_id],
        )

    def test_14_same_project_active_task_is_rejected(self):
        from web_backend.services.projects import ProjectBusy

        entered = Event()
        release = Event()

        def controlled():
            entered.set()
            release.wait(timeout=2)

        self.submit(controlled)
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.assertRaises(ProjectBusy):
                self.submit()
        finally:
            release.set()

    def test_15_different_projects_execute_independently(self):
        from web_backend.models.tasks import TaskStatus

        entered = Event()
        release = Event()

        def controlled():
            entered.set()
            release.wait(timeout=2)

        first = self.submit(controlled, project_id="project-a")
        self.assertTrue(entered.wait(timeout=1))
        second = self.submit(project_id="project-b")
        succeeded = self.wait_for_status(second.task_id, TaskStatus.SUCCEEDED)
        self.assertEqual(succeeded.project_id, "project-b")
        release.set()
        self.wait_for_status(first.task_id, TaskStatus.SUCCEEDED)

    def test_16_runner_reuses_supplied_project_lock_manager(self):
        from web_backend.models.tasks import TaskStatus

        task = self.submit()
        self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        self.assertIn("project-a", self.lock_manager.acquired)

    def test_17_task_exception_releases_project_lock(self):
        from web_backend.models.tasks import TaskStatus

        task = self.submit(lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        self.wait_for_status(task.task_id, TaskStatus.FAILED)
        with self.lock_manager.project_write("project-a"):
            pass

    def test_18_executor_shutdown_is_idempotent_and_rejects_submit(self):
        from web_backend.services.task_runner import TaskRunnerClosed

        self.runner.shutdown()
        self.runner.shutdown()
        self.assertTrue(self.runner.is_shutdown)
        with self.assertRaises(TaskRunnerClosed):
            self.runner.submit(f"task_{'0' * 32}", lambda: None)

    def test_19_restart_marks_running_task_interrupted(self):
        from web_backend.app import create_app
        from web_backend.models.tasks import TaskStatus
        from web_backend.settings import BackendSettings

        record = self.task_repository.create(self.record_for(status=TaskStatus.RUNNING))
        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
            ),
            lock_manager=self.lock_manager,
        )
        with TestClient(application):
            recovered = application.state.task_repository.get(record.task_id)
            self.assertEqual(recovered.status, TaskStatus.INTERRUPTED)
            self.assertEqual(recovered.error.code, "TASK_INTERRUPTED")

    def test_20_restart_marks_queued_task_interrupted_without_execution(self):
        from web_backend.app import create_app
        from web_backend.models.tasks import TaskStatus
        from web_backend.settings import BackendSettings

        record = self.task_repository.create(self.record_for())
        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
            ),
            lock_manager=self.lock_manager,
        )
        with TestClient(application):
            recovered = application.state.task_repository.get(record.task_id)
            self.assertEqual(recovered.status, TaskStatus.INTERRUPTED)
            self.assertIsNone(recovered.started_at)

    def test_21_restart_recovery_never_submits_callable(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.task_repository.create(self.record_for())
        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
            ),
            lock_manager=self.lock_manager,
        )
        with patch.object(application.state.task_runner, "submit") as submit:
            with TestClient(application):
                pass
        submit.assert_not_called()

    def test_22_get_missing_runtime_does_not_create_directories(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        isolated_projects = self.root / "isolated-projects"
        isolated_runtime = isolated_projects / ".web_runtime"
        application = create_app(
            settings=BackendSettings(
                projects_root=isolated_projects,
                runtime_root=isolated_runtime,
            )
        )
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(application.state.task_runner.shutdown)
        self.addCleanup(client.close)
        response = client.get(f"/api/tasks/task_{'1' * 32}")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(isolated_runtime.exists())

    def test_23_first_submit_lazily_creates_runtime(self):
        self.assertFalse(self.runtime_root.exists())
        self.submit()
        self.assertTrue((self.runtime_root / "tasks").is_dir())

    def test_24_project_discovery_ignores_web_runtime(self):
        fake = self.runtime_root / "runtime-project"
        write_json(fake / "project.json", base_project(project_id="runtime-project"))
        ids = {
            project.project_id
            for project in self.project_repository.list_projects().projects
        }
        self.assertEqual(ids, {"project-a", "project-b"})

    def test_25_task_dto_contains_no_absolute_path(self):
        record = self.task_repository.create(self.record_for())
        _, client = self.client_for()
        payload = client.get(f"/api/tasks/{record.task_id}").json()
        assert_public_payload(self, payload)

    def test_26_task_dto_contains_no_secret(self):
        record = self.task_repository.create(self.record_for())
        _, client = self.client_for()
        rendered = json.dumps(
            client.get(f"/api/tasks/{record.task_id}").json(),
            ensure_ascii=False,
        ).casefold()
        for marker in ("api_key", "authorization", "credential", "provider raw"):
            self.assertNotIn(marker, rendered)

    def test_27_persisted_json_contains_no_credentials_or_request_body(self):
        task = self.submit()
        rendered = (self.runtime_root / "tasks" / f"{task.task_id}.json").read_text(
            encoding="utf-8"
        ).casefold()
        for marker in ("credential", "authorization", "api_key", "user_notes"):
            self.assertNotIn(marker, rendered)

    def test_28_persisted_failure_contains_no_traceback(self):
        from web_backend.models.tasks import TaskStatus

        task = self.submit(lambda: (_ for _ in ()).throw(RuntimeError("raw")))
        self.wait_for_status(task.task_id, TaskStatus.FAILED)
        rendered = (self.runtime_root / "tasks" / f"{task.task_id}.json").read_text(
            encoding="utf-8"
        ).casefold()
        self.assertNotIn("traceback", rendered)
        self.assertNotIn("runtimeerror", rendered)

    def test_29_task_id_traversal_is_rejected(self):
        from web_backend.repositories.task_repository import InvalidTaskId

        for value in (
            "..",
            "%2e%2e",
            "task_../secret",
            r"C:\private",
            r"\\server\share",
            "task_" + "g" * 32,
        ):
            with self.subTest(value=value), self.assertRaises(InvalidTaskId):
                self.task_repository.get(value)

    def test_30_project_id_traversal_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId
        from web_backend.models.tasks import TaskOperation

        for value in ("..", "%2e%2e", r"C:\private", r"\\server\share"):
            with self.subTest(value=value), self.assertRaises(InvalidProjectId):
                self.service.submit(
                    project_id=value,
                    operation=TaskOperation.CREATIVE_GENERATE,
                    correlation_id="req_test",
                    callable_=lambda: None,
                )

    def test_31_task_runner_imports_and_executes_no_provider(self):
        from web_backend.models.tasks import TaskStatus

        before = {name for name in list(__import__("sys").modules) if name.startswith("providers.")}
        task = self.submit()
        self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        after = {name for name in list(__import__("sys").modules) if name.startswith("providers.")}
        self.assertEqual(after, before)

    def test_32_task_runner_sends_no_network(self):
        from web_backend.models.tasks import TaskStatus

        with (
            patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network")),
            patch.object(socket, "create_connection", side_effect=AssertionError("network")),
        ):
            task = self.submit()
            self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)

    def test_33_task_runner_runs_no_ffmpeg_or_subprocess(self):
        from web_backend.models.tasks import TaskStatus

        with (
            patch.object(subprocess, "run", side_effect=AssertionError("process")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process")),
            patch.object(os, "system", side_effect=AssertionError("process")),
        ):
            task = self.submit()
            self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)

    def test_34_tasks_do_not_modify_project_json(self):
        before = tree_snapshot(self.project_a)
        task = self.submit()
        from web_backend.models.tasks import TaskStatus

        self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        self.assertEqual(tree_snapshot(self.project_a), before)

    def test_35_tasks_do_not_modify_shot_json(self):
        from web_backend.models.tasks import TaskStatus

        shot_file = self.project_a / "shots" / "shot_01" / "shot.json"
        write_json(shot_file, {"status": "APPROVED"})
        before = tree_snapshot(self.project_a)
        task = self.submit()
        self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        self.assertEqual(tree_snapshot(self.project_a), before)

    def test_36_tasks_do_not_modify_manifests(self):
        from web_backend.models.tasks import TaskStatus

        manifest = self.project_a / "voice" / "voice_manifest.json"
        write_json(manifest, {"active_version": None, "versions": []})
        before = tree_snapshot(self.project_a)
        task = self.submit()
        self.wait_for_status(task.task_id, TaskStatus.SUCCEEDED)
        self.assertEqual(tree_snapshot(self.project_a), before)

    def test_37_task_settings_are_local_and_environment_configurable(self):
        from web_backend.settings import BackendSettings

        with patch.dict(
            os.environ,
            {
                "WEB_PROJECTS_ROOT": str(self.projects_root),
                "WEB_RUNTIME_ROOT": str(self.runtime_root),
                "WEB_TASK_WORKERS": "3",
            },
            clear=False,
        ):
            settings = BackendSettings.from_environment()
        self.assertEqual(settings.web_runtime_root, self.runtime_root)
        self.assertEqual(settings.task_workers, 3)
        derived = BackendSettings(projects_root=self.root / "derived")
        self.assertEqual(
            derived.web_runtime_root,
            self.root / "derived" / ".web_runtime",
        )

    def test_38_lifespan_shutdown_closes_task_runner(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
            ),
            lock_manager=self.lock_manager,
        )
        with TestClient(application):
            runner = application.state.task_runner
            self.assertFalse(runner.is_shutdown)
        self.assertTrue(runner.is_shutdown)

    def test_39_no_task_submit_or_cancel_api_is_published(self):
        _, client = self.client_for()
        schema = client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]["/api/tasks/{task_id}"]), {"get"})
        self.assertNotIn("/api/tasks", schema["paths"])
        self.assertFalse(
            any("cancel" in path.casefold() for path in schema["paths"])
        )

    def test_40_project_task_get_on_empty_runtime_is_read_only(self):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        isolated_runtime = self.root / "empty-runtime"
        application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=isolated_runtime,
            ),
            lock_manager=self.lock_manager,
        )
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(application.state.task_runner.shutdown)
        self.addCleanup(client.close)
        response = client.get("/api/projects/project-a/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"project_id": "project-a", "tasks": []})
        self.assertFalse(isolated_runtime.exists())


if __name__ == "__main__":
    unittest.main()
