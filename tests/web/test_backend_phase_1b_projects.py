from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient


STAGE_NAMES = (
    "CREATIVE",
    "CREATIVE_REVIEW",
    "STORYBOARD",
    "STORYBOARD_REVIEW",
    "VIDEO_PROMPT",
    "PROMPT_REVIEW",
    "VIDEO_GENERATION",
    "COMPLETED",
)
WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")


def base_project(
    *,
    project_id: str | None = "project-stable-id",
    project_name: str = "测试项目",
    updated_at: str = "2026-08-18T10:00:00+08:00",
) -> dict:
    data = {
        "project_schema_version": 2,
        "project_name": project_name,
        "created_at": "2026-08-18T09:00:00+08:00",
        "updated_at": updated_at,
        "status": "RUNNING",
        "completion_status": "NOT_STARTED",
        "current_stage": "CREATIVE",
        "cancel_stage": "",
        "cancelled_at": None,
        "last_error": None,
        "request": {
            "product_name": "柠檬饮料",
            "product_description": "清爽产品介绍",
            "user_notes": "突出自然风格",
            "duration_seconds": 18,
            "video_style": "清新",
            "video_purpose": "产品宣传",
        },
        "stages": {
            name: {
                "status": "NOT_STARTED",
                "started_at": None,
                "completed_at": None,
                "approved_at": None,
                "updated_at": updated_at,
                "attempts": 0,
            }
            for name in STAGE_NAMES
        },
        "video_generation": {
            "shot_review_schema_version": 2,
            "completed_shots": [],
            "shots": {},
        },
        "assembly": {
            "status": "NOT_STARTED",
            "needs_update": False,
            "final_video_version": None,
        },
        "post_production": {
            "status": "NOT_STARTED",
            "video_status": "NOT_STARTED",
            "current_stage": "VIDEO_ASSEMBLY",
            "stages": {},
            "components": {
                name: {
                    "status": "NOT_STARTED",
                    "active_version": None,
                    "path": None,
                    "updated_at": None,
                    "last_error": None,
                }
                for name in ("voice", "subtitle", "music", "final_export")
            },
        },
        "revision_history": [],
    }
    if project_id is not None:
        data["project_id"] = project_id
    return data


def complete_pre_assembly_project(**kwargs) -> dict:
    data = base_project(**kwargs)
    for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT", "VIDEO_GENERATION", "COMPLETED"):
        data["stages"][stage]["status"] = "COMPLETED"
    for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
        data["stages"][stage]["status"] = "APPROVED"
    data["current_stage"] = "COMPLETED"
    data["status"] = "COMPLETED"
    data["completion_status"] = "VIDEO_GENERATION_COMPLETED"
    data["video_generation"] = {
        "shot_review_schema_version": 2,
        "completed_shots": [1, 2],
        "shots": {
            "1": {"status": "APPROVED", "approved_video_version": 1},
            "2": {"status": "APPROVED", "approved_video_version": 1},
        },
    }
    return data


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_project(root: Path, directory_name: str, data: dict) -> Path:
    project_dir = root / directory_name
    write_json(project_dir / "project.json", data)
    return project_dir


def active_manifest(schema_key: str) -> dict:
    return {schema_key: 1, "active_version": 1, "versions": [{"version": 1}]}


def export_manifest(*, assembly_version: int = 1) -> dict:
    return {
        "export_schema_version": 1,
        "active_version": 1,
        "versions": [
            {
                "version": 1,
                "assembly_version": assembly_version,
                "created_at": "2026-08-18T11:00:00+08:00",
                "final_video_path": r"exports\v001\final_video.mp4",
                "credential_env_name": "MUST_NOT_ESCAPE",
            }
        ],
    }


def tree_snapshot(root: Path) -> tuple[tuple[str, ...], dict[str, tuple[str, int, int]]]:
    directories = tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_dir()
        )
    )
    files: dict[str, tuple[str, int, int]] = {}
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        content = path.read_bytes()
        stat_result = path.stat()
        files[path.relative_to(root).as_posix()] = (
            hashlib.sha256(content).hexdigest(),
            stat_result.st_mtime_ns,
            stat_result.st_size,
        )
    return directories, files


class WebBackendPhase1BProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"

    def client_for(self, root: Path | None = None) -> TestClient:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        application = create_app(
            settings=BackendSettings(projects_root=root or self.projects_root)
        )
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def repository_for(self, root: Path | None = None):
        from web_backend.repositories.project_repository import ProjectRepository

        return ProjectRepository(root or self.projects_root)

    def assert_project_error(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "PROJECT_ERROR")
        self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(
            payload["error"]["correlation_id"],
            response.headers["X-Correlation-ID"],
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIsNone(WINDOWS_PATH.search(serialized))
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("JSONDecodeError", serialized)
        return payload

    def assert_response_is_public(self, response) -> None:
        serialized = json.dumps(response.json(), ensure_ascii=False)
        self.assertIsNone(WINDOWS_PATH.search(serialized))
        for forbidden in (
            "projects_root",
            "credential_env_name",
            "MUST_NOT_ESCAPE",
            "raw_error",
            "Authorization",
            "API_KEY",
        ):
            self.assertNotIn(forbidden, serialized)

    def project_id_from_list(self, client: TestClient) -> str:
        projects = client.get("/api/projects").json()["projects"]
        self.assertEqual(len(projects), 1)
        return projects[0]["project_id"]

    def test_01_missing_projects_root_returns_empty_without_creating_it(self):
        missing = self.projects_root / "does-not-exist"
        response = self.client_for(missing).get("/api/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"projects": []})
        self.assertFalse(missing.exists())

    def test_02_only_direct_child_project_json_is_discovered(self):
        write_project(self.projects_root, "direct", base_project(project_id="direct-id"))
        write_project(
            self.projects_root / "container",
            "nested",
            base_project(project_id="nested-id"),
        )
        projects = self.client_for().get("/api/projects").json()["projects"]
        self.assertEqual([item["project_id"] for item in projects], ["direct-id"])

    def test_03_ordinary_directories_are_ignored(self):
        (self.projects_root / "ordinary").mkdir(parents=True)
        write_project(self.projects_root, "agent", base_project(project_id="agent-id"))
        projects = self.client_for().get("/api/projects").json()["projects"]
        self.assertEqual(len(projects), 1)

    def test_04_logs_and_staging_directories_are_ignored(self):
        write_project(self.projects_root, "logs", base_project(project_id="logs-id"))
        write_project(self.projects_root, "staging", base_project(project_id="stage-id"))
        response = self.client_for().get("/api/projects")
        self.assertEqual(response.json(), {"projects": []})

    def test_05_project_list_order_is_updated_desc_then_id(self):
        write_project(
            self.projects_root,
            "old",
            base_project(project_id="z-id", updated_at="2026-08-17T10:00:00+08:00"),
        )
        write_project(
            self.projects_root,
            "new-b",
            base_project(project_id="b-id", updated_at="2026-08-18T10:00:00+08:00"),
        )
        write_project(
            self.projects_root,
            "new-a",
            base_project(project_id="a-id", updated_at="2026-08-18T10:00:00+08:00"),
        )
        projects = self.client_for().get("/api/projects").json()["projects"]
        self.assertEqual([item["project_id"] for item in projects], ["a-id", "b-id", "z-id"])

    def test_06_chinese_directory_is_legacy_project_id(self):
        write_project(self.projects_root, "柠檬", base_project(project_id=None))
        projects = self.client_for().get("/api/projects").json()["projects"]
        self.assertEqual(projects[0]["project_id"], "柠檬")

    def test_07_unique_existing_project_id_is_preferred(self):
        write_project(self.projects_root, "目录名", base_project(project_id="stable-123"))
        self.assertEqual(self.project_id_from_list(self.client_for()), "stable-123")

    def test_08_duplicate_existing_ids_fall_back_to_unique_directory_names(self):
        write_project(self.projects_root, "主项目", base_project(project_id="duplicate"))
        write_project(self.projects_root, "备份项目", base_project(project_id="duplicate"))
        ids = {
            item["project_id"]
            for item in self.client_for().get("/api/projects").json()["projects"]
        }
        self.assertEqual(ids, {"主项目", "备份项目"})

    def test_09_legal_chinese_project_id_resolves(self):
        write_project(self.projects_root, "柠檬", base_project(project_id=None))
        response = self.client_for().get("/api/projects/柠檬")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "柠檬")

    def test_10_dot_dot_project_id_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir("..")

    def test_11_forward_slash_traversal_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir("../outside")

    def test_12_backslash_traversal_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir(r"..\outside")

    def test_13_windows_absolute_path_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir(r"D:\secret")

    def test_14_unc_path_is_rejected(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir(r"\\server\share")

    def test_15_encoded_traversal_is_rejected(self):
        response = self.client_for().get("/api/projects/%252e%252e")
        self.assert_project_error(response, 422, "INVALID_PROJECT_ID")

    def test_16_projects_root_outside_path_cannot_be_accessed(self):
        from web_backend.repositories.project_repository import InvalidProjectId

        outside = Path(self.temp.name) / "outside"
        write_project(outside, "secret", base_project(project_id="outside-id"))
        with self.assertRaises(InvalidProjectId):
            self.repository_for().resolve_project_dir("../outside/secret")

    def test_17_list_endpoint_returns_200(self):
        write_project(self.projects_root, "project", base_project())
        self.assertEqual(self.client_for().get("/api/projects").status_code, 200)

    def test_18_list_response_contains_no_absolute_path(self):
        write_project(
            self.projects_root,
            "project",
            base_project(project_name=r"D:\private\project"),
        )
        response = self.client_for().get("/api/projects")
        self.assert_response_is_public(response)

    def test_19_corrupt_project_does_not_break_list(self):
        corrupt_dir = self.projects_root / "损坏项目"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "project.json").write_text("{not json", encoding="utf-8")
        write_project(self.projects_root, "正常项目", base_project(project_id="good-id"))
        response = self.client_for().get("/api/projects")
        self.assertEqual(response.status_code, 200)
        projects = response.json()["projects"]
        self.assertEqual(len(projects), 2)
        unreadable = next(item for item in projects if item["status"] == "UNREADABLE")
        self.assertEqual(unreadable["workflow_phase"], "ERROR")

    def test_20_list_uses_explicit_summary_dto(self):
        data = base_project()
        data["raw_error"] = r"D:\private\traceback"
        data["provider_routing"] = {"credential_env_name": "MINIMAX_API_KEY"}
        write_project(self.projects_root, "project", data)
        item = self.client_for().get("/api/projects").json()["projects"][0]
        self.assertEqual(
            set(item),
            {"project_id", "name", "workflow_phase", "status", "updated_at", "assembly", "final_export"},
        )

    def test_21_valid_project_detail_returns_200(self):
        write_project(self.projects_root, "project", base_project())
        client = self.client_for()
        project_id = self.project_id_from_list(client)
        response = client.get(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200)

    def test_22_missing_project_returns_safe_404(self):
        response = self.client_for().get("/api/projects/not-present")
        self.assert_project_error(response, 404, "PROJECT_NOT_FOUND")

    def test_23_corrupt_project_returns_safe_error(self):
        project_dir = self.projects_root / "损坏"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text("{bad", encoding="utf-8")
        response = self.client_for().get("/api/projects/损坏")
        self.assert_project_error(response, 422, "PROJECT_DATA_CORRUPT")

    def test_24_unsupported_project_schema_returns_safe_error(self):
        data = base_project(project_id=None)
        data["project_schema_version"] = 999
        write_project(self.projects_root, "未来项目", data)
        response = self.client_for().get("/api/projects/未来项目")
        self.assert_project_error(response, 422, "PROJECT_DATA_UNSUPPORTED")

    def test_25_detail_uses_explicit_dto_not_raw_json(self):
        data = base_project()
        data.update(
            {
                "raw_error": "internal",
                "provider_routing": {"provider": "secret-provider"},
                "debug": {"path": r"D:\debug"},
            }
        )
        write_project(self.projects_root, "project", data)
        client = self.client_for()
        detail = client.get(f"/api/projects/{self.project_id_from_list(client)}").json()
        self.assertEqual(
            set(detail),
            {"project_id", "name", "request", "workflow", "assembly", "post_production", "final_export", "updated_at"},
        )
        self.assertNotIn("raw_error", json.dumps(detail))
        self.assertNotIn("provider_routing", json.dumps(detail))

    def test_26_detail_filters_credentials_and_sensitive_request_text(self):
        data = base_project()
        data["credential_env_name"] = "MINIMAX_H3_API_KEY"
        data["request"]["user_notes"] = "Authorization: Bearer do-not-return"
        write_project(self.projects_root, "project", data)
        client = self.client_for()
        response = client.get(f"/api/projects/{self.project_id_from_list(client)}")
        self.assert_response_is_public(response)
        self.assertNotIn("do-not-return", json.dumps(response.json(), ensure_ascii=False))

    def test_27_creative_phase_is_derived(self):
        write_project(self.projects_root, "project", base_project())
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "CREATIVE")

    def test_28_storyboard_review_phase_is_derived(self):
        data = base_project()
        data["stages"]["CREATIVE"]["status"] = "COMPLETED"
        data["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        data["stages"]["STORYBOARD"]["status"] = "COMPLETED"
        data["stages"]["STORYBOARD_REVIEW"]["status"] = "WAITING_REVIEW"
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "STORYBOARD_REVIEW")

    def test_29_video_generation_phase_is_derived(self):
        data = complete_pre_assembly_project()
        data["stages"]["VIDEO_GENERATION"]["status"] = "RUNNING"
        data["video_generation"]["shots"]["1"]["status"] = "GENERATING"
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "VIDEO_GENERATION")

    def test_30_all_shots_approved_without_assembly_means_assembly(self):
        write_project(self.projects_root, "project", complete_pre_assembly_project())
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "ASSEMBLY")

    def test_31_completed_assembly_without_post_means_post_production(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "POST_PRODUCTION")

    def test_32_completed_post_without_export_means_final_export(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        data["post_production"]["status"] = "COMPLETED"
        for name in ("voice", "subtitle", "music"):
            data["post_production"]["components"][name]["status"] = "COMPLETED"
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "FINAL_EXPORT")

    def test_33_valid_active_export_means_completed(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        project_dir = write_project(self.projects_root, "project", data)
        write_json(project_dir / "exports" / "export_manifest.json", export_manifest())
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "COMPLETED")

    def test_34_assembly_needs_update_overrides_old_export(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": True, "final_video_version": 1}
        )
        project_dir = write_project(self.projects_root, "project", data)
        write_json(project_dir / "exports" / "export_manifest.json", export_manifest())
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "ASSEMBLY_REQUIRED")
        self.assertTrue(response.json()["stages"]["export"]["stale"])

    def test_35_failed_project_phase_is_derived(self):
        data = base_project()
        data["status"] = "FAILED"
        data["stages"]["CREATIVE"]["status"] = "FAILED"
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "FAILED")

    def test_36_cancelled_project_phase_is_derived(self):
        data = base_project()
        data["status"] = "CANCELLED"
        data["stages"]["CREATIVE"]["status"] = "CANCELLED"
        write_project(self.projects_root, "project", data)
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "CANCELLED")

    def test_37_export_for_old_assembly_is_stale_and_not_completed(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 2}
        )
        data["post_production"]["status"] = "COMPLETED"
        project_dir = write_project(self.projects_root, "project", data)
        write_json(
            project_dir / "exports" / "export_manifest.json",
            export_manifest(assembly_version=1),
        )
        response = self.client_for().get("/api/projects/project-stable-id/workflow")
        self.assertEqual(response.json()["workflow_phase"], "FINAL_EXPORT")
        self.assertEqual(response.json()["stages"]["export"]["status"], "STALE")

    def test_38_three_gets_change_no_hash_mtime_file_or_directory(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        project_dir = write_project(self.projects_root, "只读项目", data)
        write_json(
            project_dir / "videos" / "assembly_manifest.json",
            {"manifest_version": 1, "latest_assembly_version": 1, "assemblies": [{}]},
        )
        write_json(project_dir / "voice" / "voice_manifest.json", active_manifest("voice_schema_version"))
        write_json(project_dir / "subtitles" / "subtitle_manifest.json", active_manifest("subtitle_schema_version"))
        write_json(project_dir / "music" / "music_manifest.json", active_manifest("music_schema_version"))
        write_json(project_dir / "exports" / "export_manifest.json", export_manifest())
        before = tree_snapshot(project_dir)
        client = self.client_for()
        project_id = self.project_id_from_list(client)
        self.assertEqual(client.get(f"/api/projects/{project_id}").status_code, 200)
        self.assertEqual(client.get(f"/api/projects/{project_id}/workflow").status_code, 200)
        after = tree_snapshot(project_dir)
        self.assertEqual(before, after)

    def test_39_gets_never_call_project_checkpoint_save(self):
        from project_state import ProjectCheckpoint

        write_project(self.projects_root, "project", base_project())
        with patch.object(
            ProjectCheckpoint,
            "save",
            side_effect=AssertionError("ProjectCheckpoint.save called"),
        ) as save:
            client = self.client_for()
            project_id = self.project_id_from_list(client)
            client.get(f"/api/projects/{project_id}")
            client.get(f"/api/projects/{project_id}/workflow")
        save.assert_not_called()

    def test_40_gets_use_no_provider_network_or_ffmpeg(self):
        write_project(self.projects_root, "project", base_project())
        with (
            patch.object(socket, "create_connection", side_effect=AssertionError("network used")),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider HTTP used"),
            ),
            patch.object(subprocess, "run", side_effect=AssertionError("FFmpeg used")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("FFmpeg used")),
        ):
            client = self.client_for()
            project_id = self.project_id_from_list(client)
            self.assertEqual(client.get(f"/api/projects/{project_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/projects/{project_id}/workflow").status_code, 200)

    def test_41_all_project_responses_recursively_hide_paths_and_secrets(self):
        data = complete_pre_assembly_project(project_name=r"D:\private\name")
        data["request"].update(
            {
                "product_description": r"D:\private\description",
                "user_notes": "MINIMAX_API_KEY=do-not-return",
                "video_style": r"\\server\share",
                "video_purpose": "file://private/path",
            }
        )
        data["credential_env_name"] = "DEEPSEEK_API_KEY"
        project_dir = write_project(self.projects_root, "project", data)
        manifest = export_manifest()
        manifest["versions"][0]["created_at"] = r"D:\private\export-time"
        write_json(project_dir / "exports" / "export_manifest.json", manifest)
        client = self.client_for()
        project_id = self.project_id_from_list(client)
        for path in (
            "/api/projects",
            f"/api/projects/{project_id}",
            f"/api/projects/{project_id}/workflow",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assert_response_is_public(response)
            self.assertNotIn("do-not-return", json.dumps(response.json(), ensure_ascii=False))

    def test_42_correlation_id_remains_available(self):
        write_project(self.projects_root, "project", base_project())
        response = self.client_for().get(
            "/api/projects",
            headers={"X-Correlation-ID": "req_phase1b-test"},
        )
        self.assertEqual(response.headers["X-Correlation-ID"], "req_phase1b-test")

    def test_43_error_response_does_not_expose_internal_exception(self):
        project_dir = self.projects_root / "broken"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text("{ secret D:\\\\private", encoding="utf-8")
        response = self.client_for().get("/api/projects/broken")
        payload = self.assert_project_error(response, 422, "PROJECT_DATA_CORRUPT")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private", serialized)
        self.assertNotIn("Expecting", serialized)

    def test_44_phase_1a_health_endpoint_still_works(self):
        response = self.client_for().get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "ai-product-video-agent",
                "api_version": "v1",
            },
        )

    def test_45_sensitive_correlation_id_is_not_reflected(self):
        response = self.client_for().get(
            "/api/projects",
            headers={"X-Correlation-ID": "MINIMAX_API_KEY"},
        )
        self.assertRegex(response.headers["X-Correlation-ID"], r"^req_[0-9a-f]{32}$")
        self.assertNotIn("API_KEY", json.dumps(response.json(), ensure_ascii=False))

    def test_46_sensitive_project_identifiers_use_opaque_fallback(self):
        write_project(
            self.projects_root,
            "DEEPSEEK_API_KEY",
            base_project(project_id="MINIMAX_API_KEY"),
        )
        response = self.client_for().get("/api/projects")
        self.assertEqual(response.status_code, 200)
        project_id = response.json()["projects"][0]["project_id"]
        self.assertRegex(project_id, r"^legacy-[0-9a-f]{24}$")
        self.assert_response_is_public(response)

    def test_47_access_log_uses_route_template_not_dynamic_project_id(self):
        marker = "MINIMAX_API_KEY"
        with self.assertLogs("uvicorn.error.web_access", level="INFO") as captured:
            response = self.client_for().get(f"/api/projects/{marker}")
        self.assertEqual(response.status_code, 404)
        record = "\n".join(captured.output)
        self.assertIn("route=/api/projects/{project_id}", record)
        self.assertNotIn(marker, record)


if __name__ == "__main__":
    unittest.main()
