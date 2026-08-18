from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient


PROJECT_PAYLOAD = {
    "product_name": "LEE柠檬",
    "product_description": "新鲜柠檬，果径4-5cm，酸甜可口",
    "user_notes": "不要出现人物，镜头平稳",
    "duration_seconds": 18,
    "video_style": "简洁、年轻、高明度高饱和度",
    "video_purpose": "提升产品知名度",
}
WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")


def file_tree(root: Path) -> dict[str, tuple[str, int, int]]:
    snapshot: dict[str, tuple[str, int, int]] = {}
    for path in root.rglob("*") if root.exists() else ():
        stat = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.st_mtime_ns,
            stat.st_size,
        )
    return snapshot


class WebBackendPhase1CProjectCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.locking import ProjectLockManager

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.lock_manager = ProjectLockManager()

    def client_for(self) -> TestClient:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        application = create_app(
            settings=BackendSettings(projects_root=self.projects_root),
            lock_manager=self.lock_manager,
        )
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def service_for(self, *, timeout: float = 0.25):
        from web_backend.services.projects import ProjectService

        return ProjectService(
            self.projects_root,
            self.lock_manager,
            create_lock_timeout_seconds=timeout,
        )

    def post_project(self, **overrides):
        payload = {**PROJECT_PAYLOAD, **overrides}
        return self.client_for().post("/api/projects", json=payload)

    def assert_error(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(payload["error"]["code"], code)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIsNone(WINDOWS_PATH.search(serialized))
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("API_KEY", serialized)
        return payload

    def created_directory(self, project_id: str) -> Path:
        from web_backend.repositories.project_repository import ProjectRepository

        return ProjectRepository(self.projects_root).resolve_project_dir(project_id)

    def test_01_valid_project_creation_returns_201(self):
        response = self.post_project()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["workflow_phase"], "CREATIVE")
        self.assertEqual(response.json()["status"], "NOT_STARTED")

    def test_02_response_has_stable_uuid_hex_and_location(self):
        response = self.post_project()
        project_id = response.json()["project_id"]
        self.assertRegex(project_id, r"^[0-9a-f]{32}$")
        self.assertEqual(response.headers["Location"], f"/api/projects/{project_id}")

    def test_03_project_json_exists_with_matching_stable_id(self):
        response = self.post_project()
        project_id = response.json()["project_id"]
        project_file = self.created_directory(project_id) / "project.json"
        data = json.loads(project_file.read_text(encoding="utf-8"))
        self.assertEqual(data["project_id"], project_id)
        self.assertEqual(data["project_schema_version"], 2)

    def test_04_created_project_is_loadable_by_current_core(self):
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint

        response = self.post_project()
        project_dir = self.created_directory(response.json()["project_id"])
        checkpoint = ProjectCheckpoint.load(create_project_paths(project_dir))
        self.assertEqual(checkpoint.data["project_id"], response.json()["project_id"])
        self.assertEqual(checkpoint.data["current_stage"], "CREATIVE")

    def test_05_phase_1b_repository_reads_project_immediately(self):
        from web_backend.repositories.project_repository import ProjectRepository

        response = self.post_project()
        detail = ProjectRepository(self.projects_root).get_project(
            response.json()["project_id"]
        )
        self.assertEqual(detail.project_id, response.json()["project_id"])
        self.assertEqual(detail.request.product_name, PROJECT_PAYLOAD["product_name"])

    def test_06_get_detail_and_workflow_work_without_restart(self):
        client = self.client_for()
        response = client.post("/api/projects", json=PROJECT_PAYLOAD)
        project_id = response.json()["project_id"]
        self.assertEqual(client.get(f"/api/projects/{project_id}").status_code, 200)
        workflow = client.get(f"/api/projects/{project_id}/workflow")
        self.assertEqual(workflow.status_code, 200)
        self.assertEqual(workflow.json()["workflow_phase"], "CREATIVE")

    def test_07_create_does_not_generate_creative(self):
        import storyboard

        with patch.object(
            storyboard,
            "generate_creative_brief",
            side_effect=AssertionError("Creative generation called"),
        ):
            response = self.post_project()
        self.assertEqual(response.status_code, 201)
        project_dir = self.created_directory(response.json()["project_id"])
        self.assertFalse((project_dir / "concepts" / "creative_brief.json").exists())

    def test_08_create_produces_no_task_review_or_manifest_files(self):
        response = self.post_project()
        project_dir = self.created_directory(response.json()["project_id"])
        files = [path.relative_to(project_dir).as_posix() for path in project_dir.rglob("*") if path.is_file()]
        self.assertEqual(files, ["project.json"])

    def test_09_create_uses_no_provider_or_network(self):
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider HTTP used"),
            ),
        ):
            response = self.post_project()
        self.assertEqual(response.status_code, 201)

    def test_10_create_does_not_run_ffmpeg_or_subprocess(self):
        with (
            patch.object(subprocess, "run", side_effect=AssertionError("subprocess used")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess used")),
        ):
            response = self.post_project()
        self.assertEqual(response.status_code, 201)

    def test_11_empty_product_name_returns_safe_422(self):
        response = self.post_project(product_name="   ")
        self.assert_error(response, 422, "INVALID_PROJECT_NAME")
        self.assertFalse(self.projects_root.exists())

    def test_12_invalid_duration_uses_core_rule_and_returns_422(self):
        response = self.post_project(duration_seconds=15)
        payload = self.assert_error(response, 422, "INVALID_VIDEO_DURATION")
        self.assertEqual(payload["error"]["type"], "VALIDATION_ERROR")
        self.assertFalse(self.projects_root.exists())

    def test_13_long_product_name_gets_bounded_readable_directory(self):
        response = self.post_project(product_name="长" * 300)
        self.assertEqual(response.status_code, 201)
        project_dir = self.created_directory(response.json()["project_id"])
        self.assertLessEqual(len(project_dir.name), 80)
        self.assertTrue(project_dir.name.startswith("长"))

    def test_14_traversal_product_name_is_rejected_without_escape(self):
        response = self.post_project(product_name="../outside")
        self.assert_error(response, 422, "INVALID_PROJECT_NAME")
        self.assertFalse((Path(self.temp.name) / "outside").exists())

    def test_15_windows_invalid_characters_are_safely_replaced(self):
        response = self.post_project(product_name='品牌<春季>:宣传|片?*')
        self.assertEqual(response.status_code, 201)
        directory_name = self.created_directory(response.json()["project_id"]).name
        self.assertIsNone(re.search(r'[<>:"/\\|?*]', directory_name))
        self.assertIn("品牌", directory_name)

    def test_16_request_cannot_specify_output_path(self):
        payload = {**PROJECT_PAYLOAD, "output_path": r"D:\private\escape"}
        response = self.client_for().post("/api/projects", json=payload)
        self.assert_error(response, 422, "INVALID_REQUEST")
        self.assertFalse(self.projects_root.exists())

    def test_17_request_cannot_submit_provider_credentials(self):
        payload = {**PROJECT_PAYLOAD, "api_key": "do-not-accept"}
        response = self.client_for().post("/api/projects", json=payload)
        self.assert_error(response, 422, "INVALID_REQUEST")
        self.assertFalse(self.projects_root.exists())

    def test_18_two_same_name_projects_do_not_overwrite(self):
        first = self.post_project()
        first_file = self.created_directory(first.json()["project_id"]) / "project.json"
        first_sha = hashlib.sha256(first_file.read_bytes()).hexdigest()
        second = self.post_project()
        second_file = self.created_directory(second.json()["project_id"]) / "project.json"
        self.assertNotEqual(first_file.parent, second_file.parent)
        self.assertEqual(hashlib.sha256(first_file.read_bytes()).hexdigest(), first_sha)

    def test_19_collision_uses_human_readable_numeric_suffix(self):
        first = self.post_project()
        second = self.post_project()
        self.assertEqual(self.created_directory(first.json()["project_id"]).name, "LEE柠檬")
        self.assertEqual(self.created_directory(second.json()["project_id"]).name, "LEE柠檬_2")

    def test_20_collision_projects_have_distinct_ids_and_state(self):
        first = self.post_project()
        second = self.post_project()
        self.assertNotEqual(first.json()["project_id"], second.json()["project_id"])
        first_data = json.loads(
            (self.created_directory(first.json()["project_id"]) / "project.json").read_text(encoding="utf-8")
        )
        second_data = json.loads(
            (self.created_directory(second.json()["project_id"]) / "project.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(first_data["project_id"], second_data["project_id"])

    def test_21_different_projects_have_independent_locks(self):
        manager = self.lock_manager

        def acquire_other() -> bool:
            with manager.project_write("project-b", timeout_seconds=0):
                return True

        with manager.project_write("project-a"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(acquire_other).result(timeout=2))

    def test_22_same_project_double_write_is_detected(self):
        from web_backend.locking import ProjectLockBusy

        manager = self.lock_manager

        def acquire_same() -> bool:
            try:
                with manager.project_write("project-a", timeout_seconds=0):
                    return False
            except ProjectLockBusy:
                return True

        with manager.project_write("project-a"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(acquire_same).result(timeout=2))

    def test_23_busy_creation_lock_maps_to_retryable_409(self):
        with self.lock_manager.project_creation(self.projects_root):
            response = self.post_project()
        payload = self.assert_error(response, 409, "PROJECT_BUSY")
        self.assertTrue(payload["error"]["retryable"])

    def test_24_lock_release_allows_next_writer(self):
        with self.lock_manager.project_write("project-a"):
            pass
        with self.lock_manager.project_write("project-a", timeout_seconds=0):
            acquired_after_release = True
        self.assertTrue(acquired_after_release)

    def test_25_exception_always_releases_project_lock(self):
        with self.assertRaises(RuntimeError):
            with self.lock_manager.project_write("project-a"):
                raise RuntimeError("simulated write failure")
        with self.lock_manager.project_write("project-a", timeout_seconds=0):
            acquired_after_exception = True
        self.assertTrue(acquired_after_exception)

    def test_26_root_creation_lock_serializes_same_name_race(self):
        from web_backend.models.projects import ProjectCreateRequest

        service = self.service_for(timeout=2.0)
        request = ProjectCreateRequest.model_validate(PROJECT_PAYLOAD)
        barrier = Barrier(2)

        def create() -> str:
            barrier.wait(timeout=2)
            return service.create_project(request).project_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = [future.result(timeout=5) for future in (executor.submit(create), executor.submit(create))]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(
            sorted(path.name for path in self.projects_root.iterdir()),
            ["LEE柠檬", "LEE柠檬_2"],
        )

    def test_27_gets_create_no_lock_file_and_change_no_tree_entry(self):
        client = self.client_for()
        created = client.post("/api/projects", json=PROJECT_PAYLOAD).json()
        before = file_tree(self.projects_root)
        self.assertEqual(client.get("/api/projects").status_code, 200)
        self.assertEqual(client.get(f"/api/projects/{created['project_id']}").status_code, 200)
        self.assertEqual(
            client.get(f"/api/projects/{created['project_id']}/workflow").status_code,
            200,
        )
        self.assertEqual(file_tree(self.projects_root), before)
        self.assertFalse(any("lock" in path.name.casefold() for path in self.projects_root.rglob("*")))

    def test_28_core_create_failure_leaves_no_project_or_staging(self):
        with patch(
            "web_backend.services.projects._create_core_checkpoint",
            side_effect=RuntimeError("simulated Core failure"),
        ):
            response = self.post_project()
        self.assert_error(response, 500, "PROJECT_CREATE_FAILED")
        self.assertEqual(list(self.projects_root.iterdir()), [])

    def test_29_failure_never_deletes_preexisting_project_directory(self):
        existing = self.projects_root / "LEE柠檬"
        existing.mkdir(parents=True)
        sentinel = existing / "keep.txt"
        sentinel.write_text("preserve", encoding="utf-8")
        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
        with patch(
            "web_backend.services.projects._create_core_checkpoint",
            side_effect=RuntimeError("simulated Core failure"),
        ):
            response = self.post_project()
        self.assert_error(response, 500, "PROJECT_CREATE_FAILED")
        self.assertTrue(sentinel.is_file())
        self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_30_failure_cleans_only_request_owned_hidden_staging(self):
        unrelated = self.projects_root / ".unrelated"
        unrelated.mkdir(parents=True)
        with patch(
            "web_backend.services.projects._create_core_checkpoint",
            side_effect=RuntimeError("simulated Core failure"),
        ):
            response = self.post_project()
        self.assert_error(response, 500, "PROJECT_CREATE_FAILED")
        self.assertTrue(unrelated.is_dir())
        self.assertFalse(any(path.name.startswith(".web-create-") for path in self.projects_root.iterdir()))

    def test_31_create_failure_response_hides_internal_path_and_exception(self):
        with patch(
            "web_backend.services.projects._create_core_checkpoint",
            side_effect=RuntimeError(r"private D:\projects\customer API_KEY=never-return"),
        ):
            response = self.post_project()
        payload = self.assert_error(response, 500, "PROJECT_CREATE_FAILED")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("never-return", serialized)
        self.assertNotIn("RuntimeError", serialized)

    def test_32_post_response_contains_no_path_secret_or_internal_field(self):
        response = self.post_project()
        serialized = json.dumps(response.json(), ensure_ascii=False)
        self.assertIsNone(WINDOWS_PATH.search(serialized))
        for forbidden in ("API_KEY", "credential", "Authorization", "projects_root"):
            self.assertNotIn(forbidden, serialized)

    def test_33_access_log_does_not_record_request_body(self):
        marker = "private-user-note-must-not-be-logged"
        with self.assertLogs("uvicorn.error.web_access", level="INFO") as captured:
            response = self.post_project(user_notes=marker)
        self.assertEqual(response.status_code, 201)
        record = "\n".join(captured.output)
        self.assertIn("method=POST", record)
        self.assertIn("route=/api/projects", record)
        self.assertIn("status_code=201", record)
        self.assertNotIn(marker, record)
        self.assertNotIn(PROJECT_PAYLOAD["product_description"], record)

    def test_34_reserved_windows_name_is_made_safe(self):
        response = self.post_project(product_name="COM1.txt")
        self.assertEqual(response.status_code, 201)
        directory_name = self.created_directory(response.json()["project_id"]).name
        self.assertEqual(directory_name, "项目_COM1.txt")

    def test_35_configured_root_file_returns_safe_failure_without_deleting_it(self):
        self.projects_root.parent.mkdir(parents=True, exist_ok=True)
        self.projects_root.write_text("not a directory", encoding="utf-8")
        response = self.post_project()
        self.assert_error(response, 500, "PROJECT_CREATE_FAILED")
        self.assertEqual(self.projects_root.read_text(encoding="utf-8"), "not a directory")

    def test_36_absolute_and_unc_product_names_are_rejected(self):
        for unsafe_name in (r"D:\private\project", r"\\server\share\project"):
            with self.subTest(unsafe_name=unsafe_name):
                response = self.post_project(product_name=unsafe_name)
                self.assert_error(response, 422, "INVALID_PROJECT_NAME")
        self.assertFalse(self.projects_root.exists())


if __name__ == "__main__":
    unittest.main()
