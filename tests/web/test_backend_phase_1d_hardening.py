from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    complete_pre_assembly_project,
    export_manifest,
    tree_snapshot,
    write_json,
    write_project,
)
from tests.web.test_backend_phase_1c_project_create import PROJECT_PAYLOAD
from tests.web.web_response_assertions import (
    assert_boolean_leaves,
    assert_public_payload,
)


class WebBackendPhase1DHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.locking import ProjectLockManager

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.lock_manager = ProjectLockManager()

    def application_for(self, **settings_overrides):
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        return create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                **settings_overrides,
            ),
            lock_manager=self.lock_manager,
        )

    def client_for(self, application=None) -> TestClient:
        client = TestClient(
            application or self.application_for(),
            raise_server_exceptions=False,
        )
        self.addCleanup(client.close)
        return client

    def workflow_for(self, data: dict, *, with_export: bool = False) -> dict:
        project_dir = write_project(self.projects_root, "project", data)
        if with_export:
            write_json(
                project_dir / "exports" / "export_manifest.json",
                export_manifest(),
            )
        response = self.client_for().get(
            f"/api/projects/{data['project_id']}/workflow"
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_01_cors_allows_127_vite_origin(self):
        response = self.client_for().get(
            "/api/health",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:5173",
        )

    def test_02_cors_allows_localhost_vite_origin(self):
        response = self.client_for().options(
            "/api/projects",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://localhost:5173",
        )

    def test_03_cors_rejects_unlisted_origin(self):
        response = self.client_for().get(
            "/api/health",
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_04_cors_never_uses_wildcard_or_credentials(self):
        response = self.client_for().get(
            "/api/health",
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)

    def test_05_cors_settings_reject_wildcard(self):
        from web_backend.settings import BackendSettings

        with self.assertRaises(ValidationError):
            BackendSettings(cors_origins=("*",))

    def test_06_capabilities_returns_200(self):
        response = self.client_for().get("/api/capabilities")
        self.assertEqual(response.status_code, 200)

    def test_07_capabilities_contains_boolean_leaves_only(self):
        payload = self.client_for().get("/api/capabilities").json()
        assert_boolean_leaves(self, payload)

    def test_08_capabilities_exposes_no_environment_names_or_secrets(self):
        from web_backend.dependencies import get_capability_service
        from web_backend.services.capabilities import CapabilityService

        application = self.application_for()
        application.dependency_overrides[get_capability_service] = lambda: CapabilityService(
            environment={
                "DEEPSEEK_API_KEY": "private-value",
                "MINIMAX_API_KEY": "another-private-value",
            },
            which=lambda _name: None,
        )
        payload = self.client_for(application).get("/api/capabilities").json()
        assert_public_payload(self, payload)
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("DEEPSEEK_API_KEY", rendered)
        self.assertNotIn("private-value", rendered)

    def test_09_capabilities_does_not_call_provider_or_network(self):
        with (
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider network used"),
            ),
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network used"),
            ),
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("process used"),
            ),
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("process used"),
            ),
        ):
            response = self.client_for().get("/api/capabilities")
        self.assertEqual(response.status_code, 200)

    def test_10_capabilities_ffmpeg_check_uses_discovery_only(self):
        from web_backend.dependencies import get_capability_service
        from web_backend.services.capabilities import CapabilityService

        calls: list[str] = []

        def fake_which(name: str) -> str | None:
            calls.append(name)
            return f"found-{name}"

        application = self.application_for()
        application.dependency_overrides[get_capability_service] = lambda: CapabilityService(
            environment={}, which=fake_which
        )
        payload = self.client_for(application).get("/api/capabilities").json()
        self.assertEqual(calls, ["ffmpeg", "ffprobe"])
        self.assertTrue(payload["ffmpeg"]["available"])

    def test_11_capabilities_requires_ffmpeg_and_ffprobe(self):
        from web_backend.services.capabilities import CapabilityService

        service = CapabilityService(
            environment={},
            which=lambda name: "found" if name == "ffmpeg" else None,
        )
        self.assertFalse(service.get_capabilities().ffmpeg.available)

    def test_12_capabilities_response_has_no_absolute_path(self):
        assert_public_payload(
            self,
            self.client_for().get("/api/capabilities").json(),
        )

    def test_13_health_remains_exact_and_separate_from_capabilities(self):
        payload = self.client_for().get("/api/health").json()
        self.assertEqual(
            payload,
            {
                "status": "ok",
                "service": "ai-product-video-agent",
                "api_version": "v1",
            },
        )
        self.assertNotIn("capabilities", payload)

    def test_14_new_project_action_is_generate_creative(self):
        payload = self.workflow_for(base_project())
        self.assertEqual(payload["available_actions"], ["GENERATE_CREATIVE"])

    def test_15_creative_review_actions_are_explicit(self):
        data = base_project()
        data["stages"]["CREATIVE"]["status"] = "COMPLETED"
        data["stages"]["CREATIVE_REVIEW"]["status"] = "WAITING_REVIEW"
        payload = self.workflow_for(data)
        self.assertEqual(
            payload["available_actions"],
            ["APPROVE_CREATIVE", "REVISE_CREATIVE", "REGENERATE_CREATIVE"],
        )

    def test_16_storyboard_review_actions_are_explicit(self):
        data = base_project()
        data["stages"]["CREATIVE"]["status"] = "COMPLETED"
        data["stages"]["CREATIVE_REVIEW"]["status"] = "APPROVED"
        data["stages"]["STORYBOARD"]["status"] = "COMPLETED"
        data["stages"]["STORYBOARD_REVIEW"]["status"] = "WAITING_REVIEW"
        payload = self.workflow_for(data)
        self.assertEqual(
            payload["available_actions"],
            [
                "APPROVE_STORYBOARD",
                "REVISE_STORYBOARD",
                "REGENERATE_STORYBOARD",
            ],
        )

    def test_17_video_prompt_generation_and_review_actions_are_correct(self):
        data = base_project()
        for stage in ("CREATIVE", "STORYBOARD"):
            data["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW"):
            data["stages"][stage]["status"] = "APPROVED"
        generation = self.workflow_for(data)
        self.assertEqual(generation["available_actions"], ["GENERATE_VIDEO_PROMPTS"])
        data["stages"]["VIDEO_PROMPT"]["status"] = "COMPLETED"
        data["stages"]["PROMPT_REVIEW"]["status"] = "WAITING_REVIEW"
        review = self.workflow_for(data)
        self.assertEqual(
            review["available_actions"],
            [
                "APPROVE_VIDEO_PROMPTS",
                "REVISE_VIDEO_PROMPTS",
                "REGENERATE_VIDEO_PROMPTS",
            ],
        )

    def test_18_shot_generation_action_is_correct(self):
        data = complete_pre_assembly_project()
        data["stages"]["VIDEO_GENERATION"]["status"] = "RUNNING"
        data["video_generation"]["shots"]["1"]["status"] = "GENERATING"
        payload = self.workflow_for(data)
        self.assertEqual(payload["available_actions"], ["GENERATE_SHOTS"])

    def test_19_all_approved_shots_offer_assembly_and_versions(self):
        payload = self.workflow_for(complete_pre_assembly_project())
        self.assertEqual(payload["workflow_phase"], "ASSEMBLY")
        self.assertEqual(
            payload["available_actions"],
            ["ASSEMBLE", "MANAGE_SHOT_VERSIONS"],
        )

    def test_20_assembly_needs_update_never_offers_final_export(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": True, "final_video_version": 1}
        )
        payload = self.workflow_for(data, with_export=True)
        self.assertEqual(payload["workflow_phase"], "ASSEMBLY_REQUIRED")
        self.assertEqual(payload["available_actions"][0], "ASSEMBLE")
        self.assertNotIn("FINAL_EXPORT", payload["available_actions"])

    def test_21_completed_assembly_offers_post_production_actions(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        payload = self.workflow_for(data)
        self.assertEqual(
            payload["available_actions"],
            ["GENERATE_VOICE", "GENERATE_SUBTITLE", "SET_MUSIC"],
        )

    def test_22_valid_export_means_completed_and_no_actions(self):
        data = complete_pre_assembly_project()
        data["assembly"].update(
            {"status": "COMPLETED", "needs_update": False, "final_video_version": 1}
        )
        payload = self.workflow_for(data, with_export=True)
        self.assertEqual(payload["workflow_phase"], "COMPLETED")
        self.assertEqual(payload["available_actions"], [])

    def test_23_failed_and_cancelled_projects_have_no_actions(self):
        for status in ("FAILED", "CANCELLED"):
            with self.subTest(status=status):
                data = base_project(project_id=f"project-{status.casefold()}")
                data["status"] = status
                data["stages"]["CREATIVE"]["status"] = status
                self.projects_root = Path(self.temp.name) / status.casefold()
                payload = self.workflow_for(data)
                self.assertEqual(payload["available_actions"], [])

    def test_24_actions_never_expose_candidate_terminology(self):
        from web_backend.models.projects import AvailableAction

        rendered = " ".join(action.value for action in AvailableAction).casefold()
        self.assertNotIn("candidate", rendered)

    def test_25_workflow_dto_includes_updated_at_and_deterministic_actions(self):
        payload = self.workflow_for(base_project())
        self.assertEqual(payload["updated_at"], "2026-08-18T10:00:00+08:00")
        self.assertEqual(payload["available_actions"], ["GENERATE_CREATIVE"])

    def test_26_lifespan_starts_and_closes_cleanly(self):
        application = self.application_for()
        with TestClient(application, raise_server_exceptions=False) as client:
            self.assertTrue(application.state.lifecycle_started)
            self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertFalse(application.state.lifecycle_started)

    def test_27_lifespan_does_not_scan_or_modify_projects(self):
        project_dir = write_project(self.projects_root, "project", base_project())
        before = tree_snapshot(project_dir)
        application = self.application_for()
        with patch(
            "web_backend.repositories.project_repository.ProjectRepository._discover_records",
            side_effect=AssertionError("startup scanned projects"),
        ):
            with TestClient(application):
                pass
        self.assertEqual(tree_snapshot(project_dir), before)

    def test_28_lifespan_does_not_call_provider_ffmpeg_or_network(self):
        application = self.application_for()
        provider_modules = {
            name for name in sys.modules if name.startswith("providers.")
        }
        with (
            patch.object(requests.sessions.Session, "request") as request,
            patch.object(subprocess, "run") as run,
            patch.object(subprocess, "Popen") as popen,
            patch.object(socket, "create_connection") as connect,
        ):
            with TestClient(application):
                pass
        request.assert_not_called()
        run.assert_not_called()
        popen.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(
            provider_modules,
            {name for name in sys.modules if name.startswith("providers.")},
        )

    def test_29_repository_service_and_lock_manager_are_app_scoped(self):
        application = self.application_for()
        with TestClient(application) as client:
            repository = application.state.project_repository
            service = application.state.project_service
            lock_manager = application.state.project_lock_manager
            client.get("/api/health")
            client.get("/api/projects")
            self.assertIs(application.state.project_repository, repository)
            self.assertIs(application.state.project_service, service)
            self.assertIs(application.state.project_lock_manager, lock_manager)

    def test_30_non_loopback_host_emits_warning(self):
        application = self.application_for(host="0.0.0.0")
        with self.assertLogs("uvicorn.error.web_lifecycle", level="WARNING"):
            with TestClient(application):
                pass

    def test_31_dependency_override_can_replace_repository(self):
        from web_backend.dependencies import get_project_repository
        from web_backend.models.projects import ProjectListResponse

        class EmptyRepository:
            def list_projects(self):
                return ProjectListResponse(projects=[])

        application = self.application_for()
        application.dependency_overrides[get_project_repository] = EmptyRepository
        response = self.client_for(application).get("/api/projects")
        self.assertEqual(response.json(), {"projects": []})

    def test_32_public_project_write_transaction_reports_busy(self):
        from web_backend.locking import ProjectLockBusy

        def try_lock() -> bool:
            try:
                with self.lock_manager.project_write("same-project"):
                    return True
            except ProjectLockBusy:
                return False

        with self.lock_manager.project_write("same-project"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertFalse(executor.submit(try_lock).result(timeout=2))

    def test_33_project_write_transaction_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            with self.lock_manager.project_write("release-project"):
                raise RuntimeError("simulated")
        with self.lock_manager.project_write("release-project"):
            pass

    def test_34_different_project_write_transactions_are_independent(self):
        def lock_other() -> bool:
            with self.lock_manager.project_write("project-b"):
                return True

        with self.lock_manager.project_write("project-a"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(lock_other).result(timeout=2))

    def test_35_write_and_get_locks_create_no_lock_files(self):
        self.projects_root.mkdir(parents=True)
        before = tree_snapshot(self.projects_root)
        with self.lock_manager.project_write("project"):
            pass
        self.assertEqual(tree_snapshot(self.projects_root), before)
        self.assertEqual(self.client_for().get("/api/projects").status_code, 200)
        self.assertEqual(tree_snapshot(self.projects_root), before)

    def test_36_project_busy_is_409_retryable_with_correlation(self):
        acquired = Event()
        release = Event()

        def hold_creation_lock() -> None:
            with self.lock_manager.project_creation(self.projects_root):
                acquired.set()
                release.wait(timeout=3)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(hold_creation_lock)
            self.assertTrue(acquired.wait(timeout=2))
            response = self.client_for().post("/api/projects", json=PROJECT_PAYLOAD)
            release.set()
            future.result(timeout=2)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["error"]["retryable"])
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertEqual(
            response.json()["error"]["correlation_id"],
            response.headers["X-Correlation-ID"],
        )

    def test_37_404_422_and_500_errors_use_safe_registry_shape(self):
        application = self.application_for()

        @application.get("/_test/failure")
        async def failure() -> None:
            raise RuntimeError(r"private D:\internal API_KEY=never-return")

        client = self.client_for(application)
        with self.assertLogs("uvicorn.error.web_errors", level="ERROR"):
            internal_error = client.get("/_test/failure")
        responses = (
            client.get("/api/missing"),
            client.get("/api/projects/%2E%2E"),
            internal_error,
        )
        self.assertEqual([response.status_code for response in responses], [404, 422, 500])
        for response in responses:
            assert_public_payload(self, response.json())
            self.assertEqual(set(response.json()), {"error"})

    def test_38_security_headers_are_present_on_success_and_error(self):
        client = self.client_for()
        for response in (client.get("/api/health"), client.get("/api/missing")):
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_39_correlation_id_rejects_sensitive_or_injectable_values(self):
        client = self.client_for()
        for supplied in ("API_KEY-secret", "line\r\nbreak", "x" * 65):
            response = client.get(
                "/api/missing", headers={"X-Correlation-ID": supplied}
            )
            self.assertNotEqual(response.headers["X-Correlation-ID"], supplied)
            self.assertEqual(
                response.headers["X-Correlation-ID"],
                response.json()["error"]["correlation_id"],
            )

    def test_40_openapi_contains_no_secret_or_absolute_path(self):
        payload = self.client_for().get("/openapi.json").json()
        assert_public_payload(self, payload)
        rendered = json.dumps(payload, ensure_ascii=False)
        for forbidden in ("credential_env_name", "local_path", "raw_error"):
            self.assertNotIn(forbidden, rendered)

    def test_41_public_methods_are_limited_to_get_post_and_options(self):
        schema = self.client_for().get("/openapi.json").json()
        methods = {
            method.upper()
            for path, operations in schema["paths"].items()
            if path.startswith("/api/")
            for method in operations
        }
        self.assertEqual(methods, {"GET", "POST"})
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertEqual(
                self.client_for().request(method, "/api/projects").status_code,
                405,
            )

    def test_42_recursive_public_response_audit_covers_all_current_apis(self):
        client = self.client_for()
        created = client.post("/api/projects", json=PROJECT_PAYLOAD)
        self.assertEqual(created.status_code, 201)
        project_id = created.json()["project_id"]
        responses = (
            client.get("/api/health"),
            client.get("/api/capabilities"),
            client.get("/api/projects"),
            client.get(f"/api/projects/{project_id}"),
            client.get(f"/api/projects/{project_id}/workflow"),
            created,
        )
        for response in responses:
            self.assertLess(response.status_code, 400)
            assert_public_payload(self, response.json())


if __name__ == "__main__":
    unittest.main()
