from __future__ import annotations

import os
import socket
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1c_project_create import PROJECT_PAYLOAD
from web_backend.settings import (
    ROOT_ENV_PATH,
    BackendSettings,
    load_root_environment,
)


class ReleaseConfigBootstrapTests(unittest.TestCase):
    @staticmethod
    def write_env(path: Path, **values: str) -> None:
        path.write_text(
            "".join(f"{name}={value}\n" for name, value in values.items()),
            encoding="utf-8",
        )

    @staticmethod
    def create_app():
        from web_backend.app import create_app

        return create_app()

    def test_01_missing_env_and_process_values_use_portable_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            portable_home = root / "portable-home"
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.ROOT_ENV_PATH", root / "missing.env"),
                patch("web_backend.settings.Path.home", return_value=portable_home),
            ):
                load_root_environment()
                settings = BackendSettings.from_environment()
        self.assertEqual(
            settings.projects_root,
            portable_home / "AIProductVideoAgentProjects",
        )

    def test_02_root_env_projects_root_is_adopted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "dotenv-projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=projects_root.as_posix())
            with patch.dict(os.environ, {}, clear=True):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.projects_root, projects_root)

    def test_03_app_bootstrap_precedes_default_settings_construction(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "app-projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=projects_root.as_posix())
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.ROOT_ENV_PATH", env_path),
            ):
                application = self.create_app()
                self.addCleanup(application.state.task_runner.shutdown)
                self.assertEqual(application.state.settings.projects_root, projects_root)

    def test_04_process_projects_root_overrides_dotenv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            dotenv_root = root / "dotenv-projects"
            process_root = root / "process-projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=dotenv_root.as_posix())
            with patch.dict(
                os.environ,
                {"WEB_PROJECTS_ROOT": str(process_root)},
                clear=True,
            ):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.projects_root, process_root)

    def test_05_blank_dotenv_projects_root_uses_portable_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            portable_home = root / "portable-home"
            self.write_env(env_path, WEB_PROJECTS_ROOT="")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.Path.home", return_value=portable_home),
            ):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(
            settings.projects_root,
            portable_home / "AIProductVideoAgentProjects",
        )

    def test_06_blank_dotenv_runtime_root_follows_projects_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            self.write_env(
                env_path,
                WEB_PROJECTS_ROOT=projects_root.as_posix(),
                WEB_RUNTIME_ROOT="",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.web_runtime_root, projects_root / ".web_runtime")

    def test_07_explicit_dotenv_runtime_root_is_adopted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            runtime_root = root / "dotenv-runtime"
            self.write_env(
                env_path,
                WEB_PROJECTS_ROOT=projects_root.as_posix(),
                WEB_RUNTIME_ROOT=runtime_root.as_posix(),
            )
            with patch.dict(os.environ, {}, clear=True):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.web_runtime_root, runtime_root)

    def test_08_process_runtime_root_overrides_dotenv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            dotenv_root = root / "dotenv-runtime"
            process_root = root / "process-runtime"
            self.write_env(
                env_path,
                WEB_PROJECTS_ROOT=projects_root.as_posix(),
                WEB_RUNTIME_ROOT=dotenv_root.as_posix(),
            )
            with patch.dict(
                os.environ,
                {"WEB_RUNTIME_ROOT": str(process_root)},
                clear=True,
            ):
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.web_runtime_root, process_root)

    def test_09_app_resources_share_bootstrapped_roots(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            runtime_root = root / "runtime"
            self.write_env(
                env_path,
                WEB_PROJECTS_ROOT=projects_root.as_posix(),
                WEB_RUNTIME_ROOT=runtime_root.as_posix(),
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.ROOT_ENV_PATH", env_path),
            ):
                application = self.create_app()
                self.addCleanup(application.state.task_runner.shutdown)
                self.assertEqual(application.state.settings.projects_root, projects_root)
                self.assertEqual(
                    application.state.project_repository.projects_root,
                    projects_root,
                )
                self.assertEqual(
                    application.state.project_service.projects_root,
                    projects_root,
                )
                self.assertEqual(
                    application.state.task_repository.runtime_root,
                    runtime_root,
                )

    def test_10_create_project_writes_only_under_dotenv_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=projects_root.as_posix())
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.ROOT_ENV_PATH", env_path),
            ):
                application = self.create_app()
                with TestClient(application, raise_server_exceptions=False) as client:
                    response = client.post("/api/projects", json=PROJECT_PAYLOAD)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(len(list(projects_root.glob("*/project.json"))), 1)

    def test_11_provider_free_startup_remains_local(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            projects_root = root / "projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=projects_root.as_posix())
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.ROOT_ENV_PATH", env_path),
                patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
                patch.object(
                    requests.sessions.Session,
                    "request",
                    side_effect=AssertionError("provider used"),
                ),
                patch.object(subprocess, "run", side_effect=AssertionError("process used")),
                patch.object(subprocess, "Popen", side_effect=AssertionError("process used")),
            ):
                application = self.create_app()
                with TestClient(application, raise_server_exceptions=False) as client:
                    health = client.get("/api/health")
                    capabilities = client.get("/api/capabilities")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(capabilities.status_code, 200)
            self.assertFalse(capabilities.json()["planning"]["deepseek"]["available"])
            self.assertFalse(capabilities.json()["video"]["minimax_hailuo"]["available"])

    def test_12_dotenv_provider_value_is_visible_only_as_availability(self) -> None:
        from web_backend.services.capabilities import CapabilityService

        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            self.write_env(env_path, DEEPSEEK_API_KEY="bootstrap-test-placeholder")
            with patch.dict(os.environ, {}, clear=True):
                load_root_environment(env_path)
                payload = CapabilityService(which=lambda _name: None).get_capabilities()
        self.assertTrue(payload.planning.deepseek.available)
        self.assertNotIn("bootstrap-test-placeholder", payload.model_dump_json())

    def test_13_non_repository_cwd_does_not_change_dotenv_resolution(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / "repository" / ".env"
            unrelated_cwd = root / "elsewhere"
            projects_root = root / "projects"
            env_path.parent.mkdir()
            unrelated_cwd.mkdir()
            self.write_env(env_path, WEB_PROJECTS_ROOT=projects_root.as_posix())
            previous_cwd = Path.cwd()
            try:
                os.chdir(unrelated_cwd)
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch("web_backend.settings.ROOT_ENV_PATH", env_path),
                ):
                    application = self.create_app()
                    self.addCleanup(application.state.task_runner.shutdown)
                    configured_root = application.state.settings.projects_root
            finally:
                os.chdir(previous_cwd)
        self.assertEqual(configured_root, projects_root)

    def test_14_root_env_path_is_source_relative_and_portable(self) -> None:
        import web_backend.settings as settings_module

        expected = Path(settings_module.__file__).resolve().parent.parent / ".env"
        self.assertEqual(ROOT_ENV_PATH, expected)
        source = Path(settings_module.__file__).read_text(encoding="utf-8").casefold()
        self.assertNotIn("d:\\desktop", source.replace("/", "\\"))

    def test_15_explicit_settings_construction_is_unchanged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "explicit-projects"
            runtime_root = Path(temp_dir) / "explicit-runtime"
            settings = BackendSettings(
                projects_root=projects_root,
                runtime_root=runtime_root,
            )
        self.assertEqual(settings.projects_root, projects_root)
        self.assertEqual(settings.web_runtime_root, runtime_root)

    def test_16_repeated_bootstrap_does_not_override_existing_values(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            dotenv_root = root / "dotenv-projects"
            process_root = root / "process-projects"
            self.write_env(env_path, WEB_PROJECTS_ROOT=dotenv_root.as_posix())
            with patch.dict(os.environ, {}, clear=True):
                load_root_environment(env_path)
                os.environ["WEB_PROJECTS_ROOT"] = str(process_root)
                load_root_environment(env_path)
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.projects_root, process_root)


if __name__ == "__main__":
    unittest.main()
