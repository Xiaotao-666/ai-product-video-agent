from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ReleasePortableRuntimeRootTests(unittest.TestCase):
    def test_01_explicit_projects_root_takes_priority(self) -> None:
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "explicit-projects"
            with patch.dict(
                os.environ,
                {"WEB_PROJECTS_ROOT": str(target)},
                clear=True,
            ):
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.projects_root, target)

    def test_02_default_projects_root_uses_path_home(self) -> None:
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "portable-home"
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("web_backend.settings.Path.home", return_value=home),
            ):
                settings = BackendSettings.from_environment()
        self.assertEqual(
            settings.projects_root,
            home / "AIProductVideoAgentProjects",
        )

    def test_03_default_projects_root_contains_no_developer_path(self) -> None:
        from web_backend.settings import BackendSettings

        home = Path("C:/Users/portable-user")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("web_backend.settings.Path.home", return_value=home),
        ):
            rendered = str(BackendSettings.from_environment().projects_root)
        lowered = rendered.casefold()
        self.assertNotIn("d:\\", lowered)
        self.assertNotIn("desktop", lowered)
        self.assertNotIn("xiaoyu", lowered)

    def test_04_default_runtime_root_follows_projects_root(self) -> None:
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "projects"
            with patch.dict(
                os.environ,
                {"WEB_PROJECTS_ROOT": str(projects_root)},
                clear=True,
            ):
                settings = BackendSettings.from_environment()
        self.assertEqual(
            settings.web_runtime_root,
            projects_root / ".web_runtime",
        )

    def test_05_custom_runtime_root_takes_priority(self) -> None:
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "projects"
            runtime_root = Path(temp_dir) / "runtime"
            with patch.dict(
                os.environ,
                {
                    "WEB_PROJECTS_ROOT": str(projects_root),
                    "WEB_RUNTIME_ROOT": str(runtime_root),
                },
                clear=True,
            ):
                settings = BackendSettings.from_environment()
        self.assertEqual(settings.web_runtime_root, runtime_root)

    def test_06_missing_projects_root_is_created_by_existing_service(self) -> None:
        from web_backend.locking import ProjectLockManager
        from web_backend.models.projects import ProjectCreateRequest
        from web_backend.services.projects import ProjectService

        with TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "missing" / "projects"
            service = ProjectService(projects_root, ProjectLockManager())
            created = service.create_project(
                ProjectCreateRequest(
                    product_name="Portable Project",
                    product_description="Portable project creation test",
                    duration_seconds=18,
                    video_style="clean",
                    video_purpose="release readiness",
                )
            )
            self.assertTrue(projects_root.is_dir())
            project_files = list(projects_root.glob("*/project.json"))
            self.assertEqual(len(project_files), 1)
            self.assertEqual(created.name, "Portable Project")

    def test_07_explicit_constructor_root_is_unchanged(self) -> None:
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "constructor-root"
            settings = BackendSettings(projects_root=target)
        self.assertEqual(settings.projects_root, target)
        self.assertEqual(settings.web_runtime_root, target / ".web_runtime")


if __name__ == "__main__":
    unittest.main()
