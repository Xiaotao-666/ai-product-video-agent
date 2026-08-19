from __future__ import annotations

import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from unittest.mock import Mock, patch

from project_manager import ProjectDirectoryError, create_project_paths
from web_backend.locking import ProjectLockManager


def windows_replace_error(code: int = 5) -> PermissionError:
    error = PermissionError(13, "simulated Windows replace conflict")
    error.winerror = code
    return error


class WindowsAtomicPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = create_project_paths(self.root / "project-a")
        self.target = self.paths.project_state_path()
        self.paths.save_json(self.target, {"revision": 0})

    def read_target(self) -> dict:
        return json.loads(self.target.read_text(encoding="utf-8"))

    def test_01_temp_handle_is_closed_and_fsynced_before_replace(self):
        original_open = Path.open
        original_fsync = os.fsync
        original_replace = os.replace
        temporary_handles = []
        events: list[str] = []

        def tracking_open(path: Path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            if path.name.endswith(".tmp"):
                temporary_handles.append(handle)
            return handle

        def tracking_fsync(file_descriptor: int):
            events.append("fsync")
            return original_fsync(file_descriptor)

        def tracking_replace(source: Path, target: Path):
            self.assertEqual(events, ["fsync"])
            self.assertTrue(temporary_handles[-1].closed)
            events.append("replace")
            return original_replace(source, target)

        with (
            patch.object(Path, "open", new=tracking_open),
            patch("project_manager.os.fsync", side_effect=tracking_fsync),
            patch("project_manager.os.replace", side_effect=tracking_replace),
        ):
            self.paths.save_json(self.target, {"revision": 1})

        self.assertEqual(events, ["fsync", "replace"])
        self.assertEqual(self.read_target(), {"revision": 1})

    def test_02_each_write_uses_a_unique_same_directory_temp(self):
        original_replace = os.replace
        sources: list[Path] = []

        def record_replace(source: Path, target: Path):
            sources.append(Path(source))
            return original_replace(source, target)

        with patch("project_manager.os.replace", side_effect=record_replace):
            self.paths.save_json(self.target, {"revision": 1})
            self.paths.save_json(self.target, {"revision": 2})

        self.assertEqual(len(set(sources)), 2)
        self.assertTrue(all(source.parent == self.target.parent for source in sources))
        self.assertTrue(
            all(
                re.fullmatch(r"\.project\.json\.[0-9a-f]{32}\.tmp", source.name)
                for source in sources
            )
        )

    def test_03_same_project_lock_serializes_writers(self):
        manager = ProjectLockManager()
        first_entered = Event()
        second_attempted = Event()
        release_first = Event()
        state_guard = Lock()
        active = 0
        maximum_active = 0

        def write(label: str, *, hold: bool = False) -> None:
            nonlocal active, maximum_active
            if not hold:
                second_attempted.set()
            with manager.project_write("project-a", timeout_seconds=1):
                with state_guard:
                    active += 1
                    maximum_active = max(maximum_active, active)
                if hold:
                    first_entered.set()
                    release_first.wait(timeout=1)
                self.paths.save_json(self.target, {"writer": label})
                with state_guard:
                    active -= 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(write, "first", hold=True)
            self.assertTrue(first_entered.wait(timeout=1))
            second = executor.submit(write, "second")
            self.assertTrue(second_attempted.wait(timeout=1))
            self.assertFalse(second.done())
            release_first.set()
            first.result(timeout=2)
            second.result(timeout=2)

        self.assertEqual(maximum_active, 1)
        self.assertEqual(self.read_target(), {"writer": "second"})

    def test_04_different_projects_can_write_in_parallel(self):
        manager = ProjectLockManager()
        other_paths = create_project_paths(self.root / "project-b")
        other_target = other_paths.project_state_path()
        rendezvous = Barrier(2)
        state_guard = Lock()
        active = 0
        maximum_active = 0

        def write(project_id: str, paths, target: Path) -> None:
            nonlocal active, maximum_active
            with manager.project_write(project_id, timeout_seconds=1):
                with state_guard:
                    active += 1
                    maximum_active = max(maximum_active, active)
                rendezvous.wait(timeout=1)
                paths.save_json(target, {"project_id": project_id})
                with state_guard:
                    active -= 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(write, "project-a", self.paths, self.target),
                executor.submit(write, "project-b", other_paths, other_target),
            )
            for future in futures:
                future.result(timeout=2)

        self.assertEqual(maximum_active, 2)
        self.assertEqual(self.read_target(), {"project_id": "project-a"})
        self.assertEqual(
            json.loads(other_target.read_text(encoding="utf-8")),
            {"project_id": "project-b"},
        )

    def test_05_one_winerror_5_retries_only_replace(self):
        original_replace = os.replace
        attempts = 0

        def fail_once(source: Path, target: Path):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise windows_replace_error()
            return original_replace(source, target)

        with (
            patch("project_manager.os.replace", side_effect=fail_once),
            patch("project_manager.time.sleep") as sleeper,
        ):
            self.paths.save_json(self.target, {"revision": 1})

        self.assertEqual(attempts, 2)
        sleeper.assert_called_once_with(0.01)
        self.assertEqual(self.read_target(), {"revision": 1})

    def test_06_replace_retry_does_not_repeat_business_callable(self):
        original_replace = os.replace
        business = Mock(return_value={"revision": 1})
        payload = business()
        attempts = 0

        def fail_once(source: Path, target: Path):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise windows_replace_error(32)
            return original_replace(source, target)

        with (
            patch("project_manager.os.replace", side_effect=fail_once),
            patch("project_manager.time.sleep"),
        ):
            self.paths.save_json(self.target, payload)

        business.assert_called_once_with()
        self.assertEqual(attempts, 2)

    def test_07_replace_retry_does_not_repeat_provider_callable(self):
        original_replace = os.replace
        provider = Mock(return_value={"provider_result": "mock-only"})
        payload = provider()
        attempts = 0

        def fail_once(source: Path, target: Path):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise windows_replace_error(33)
            return original_replace(source, target)

        with (
            patch("project_manager.os.replace", side_effect=fail_once),
            patch("project_manager.time.sleep"),
        ):
            self.paths.save_json(self.target, payload)

        provider.assert_called_once_with()
        self.assertEqual(attempts, 2)

    def test_08_retry_exhaustion_fails_and_keeps_previous_json(self):
        with (
            patch(
                "project_manager.os.replace",
                side_effect=lambda *_args: (_ for _ in ()).throw(
                    windows_replace_error()
                ),
            ) as replace,
            patch("project_manager.time.sleep") as sleeper,
        ):
            with self.assertRaises(ProjectDirectoryError):
                self.paths.save_json(self.target, {"revision": 99})

        self.assertEqual(replace.call_count, 5)
        self.assertEqual(
            [call.args[0] for call in sleeper.call_args_list],
            [0.01, 0.02, 0.04, 0.08],
        )
        self.assertEqual(self.read_target(), {"revision": 0})

    def test_09_failed_write_cleans_only_its_temp(self):
        legacy_temp = self.target.with_suffix(self.target.suffix + ".tmp")
        unrelated_temp = self.target.parent / ".project.json.preexisting.tmp"
        legacy_temp.write_text("legacy", encoding="utf-8")
        unrelated_temp.write_text("unrelated", encoding="utf-8")
        attempted_sources: list[Path] = []

        def fail(source: Path, _target: Path):
            attempted_sources.append(Path(source))
            raise windows_replace_error()

        with (
            patch("project_manager.os.replace", side_effect=fail),
            patch("project_manager.time.sleep"),
        ):
            with self.assertRaises(ProjectDirectoryError):
                self.paths.save_json(self.target, {"revision": 1})

        self.assertTrue(attempted_sources)
        self.assertFalse(attempted_sources[0].exists())
        self.assertEqual(legacy_temp.read_text(encoding="utf-8"), "legacy")
        self.assertEqual(unrelated_temp.read_text(encoding="utf-8"), "unrelated")

    def test_10_non_retryable_error_is_not_retried(self):
        error = windows_replace_error(2)
        with (
            patch("project_manager.os.replace", side_effect=error) as replace,
            patch("project_manager.time.sleep") as sleeper,
        ):
            with self.assertRaises(ProjectDirectoryError):
                self.paths.save_json(self.target, {"revision": 1})

        replace.assert_called_once()
        sleeper.assert_not_called()
        self.assertEqual(self.read_target(), {"revision": 0})

    def test_11_concurrent_unique_temps_never_publish_partial_json(self):
        def write(revision: int) -> None:
            self.paths.save_json(
                self.target,
                {"revision": revision, "content": "x" * 2048},
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(write, revision) for revision in range(1, 17)]
            for future in futures:
                future.result(timeout=3)

        payload = self.read_target()
        self.assertIn(payload["revision"], range(1, 17))
        self.assertEqual(payload["content"], "x" * 2048)
        self.assertEqual(list(self.target.parent.glob(".project.json.*.tmp")), [])

    def test_12_failed_write_does_not_modify_another_project(self):
        other_paths = create_project_paths(self.root / "project-b")
        other_target = other_paths.project_state_path()
        other_paths.save_json(other_target, {"project_id": "project-b"})
        before = other_target.read_bytes()

        with (
            patch("project_manager.os.replace", side_effect=windows_replace_error()),
            patch("project_manager.time.sleep"),
        ):
            with self.assertRaises(ProjectDirectoryError):
                self.paths.save_json(self.target, {"revision": 1})

        self.assertEqual(other_target.read_bytes(), before)
        self.assertEqual(
            json.loads(other_target.read_text(encoding="utf-8")),
            {"project_id": "project-b"},
        )


if __name__ == "__main__":
    unittest.main()
