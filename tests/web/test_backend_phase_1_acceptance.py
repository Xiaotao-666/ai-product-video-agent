from __future__ import annotations

import socket
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import tree_snapshot
from tests.web.test_backend_phase_1c_project_create import PROJECT_PAYLOAD
from tests.web.web_response_assertions import assert_public_payload


class WebBackendPhase1AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.application = create_app(
            settings=BackendSettings(projects_root=self.projects_root),
            lock_manager=ProjectLockManager(),
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def create_project(self) -> dict:
        response = self.client.post("/api/projects", json=PROJECT_PAYLOAD)
        self.assertEqual(response.status_code, 201)
        return response.json()

    def project_directory(self, project_id: str) -> Path:
        from web_backend.repositories.project_repository import ProjectRepository

        return ProjectRepository(self.projects_root).resolve_project_dir(project_id)

    def test_01_health_contract_is_stable(self):
        self.assertEqual(
            self.client.get("/api/health").json(),
            {
                "status": "ok",
                "service": "ai-product-video-agent",
                "api_version": "v1",
            },
        )

    def test_02_create_list_detail_and_workflow_form_one_flow(self):
        created = self.create_project()
        project_id = created["project_id"]
        listed = self.client.get("/api/projects").json()["projects"]
        self.assertEqual([item["project_id"] for item in listed], [project_id])
        detail = self.client.get(f"/api/projects/{project_id}").json()
        workflow = self.client.get(f"/api/projects/{project_id}/workflow").json()
        self.assertEqual(detail["project_id"], project_id)
        self.assertEqual(workflow["workflow_phase"], "CREATIVE")
        self.assertEqual(workflow["available_actions"], ["GENERATE_CREATIVE"])

    def test_03_created_project_is_loadable_by_core(self):
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint

        created = self.create_project()
        checkpoint = ProjectCheckpoint.load(
            create_project_paths(self.project_directory(created["project_id"]))
        )
        self.assertEqual(checkpoint.data["project_id"], created["project_id"])
        self.assertEqual(checkpoint.data["current_stage"], "CREATIVE")

    def test_04_all_current_json_responses_pass_recursive_privacy_audit(self):
        created_response = self.client.post("/api/projects", json=PROJECT_PAYLOAD)
        project_id = created_response.json()["project_id"]
        responses = (
            self.client.get("/api/health"),
            self.client.get("/api/capabilities"),
            self.client.get("/api/projects"),
            self.client.get(f"/api/projects/{project_id}"),
            self.client.get(f"/api/projects/{project_id}/workflow"),
            created_response,
        )
        for response in responses:
            self.assertLess(response.status_code, 400)
            assert_public_payload(self, response.json())

    def test_05_all_gets_are_zero_side_effect(self):
        created = self.create_project()
        project_dir = self.project_directory(created["project_id"])
        before = tree_snapshot(project_dir)
        self.client.get("/api/health")
        self.client.get("/api/capabilities")
        self.client.get("/api/projects")
        self.client.get(f"/api/projects/{created['project_id']}")
        self.client.get(f"/api/projects/{created['project_id']}/workflow")
        self.assertEqual(tree_snapshot(project_dir), before)

    def test_06_create_and_capabilities_make_no_real_calls(self):
        with (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network used"),
            ),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider used"),
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
            self.assertEqual(
                self.client.get("/api/capabilities").status_code,
                200,
            )
            self.assertEqual(
                self.client.post("/api/projects", json=PROJECT_PAYLOAD).status_code,
                201,
            )

    def test_07_error_correlation_and_security_headers_are_consistent(self):
        response = self.client.get(
            "/api/not-found",
            headers={"X-Correlation-ID": "req_acceptance"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Correlation-ID"], "req_acceptance")
        self.assertEqual(
            response.json()["error"]["correlation_id"], "req_acceptance"
        )
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_08_cors_and_openapi_are_frontend_ready_and_safe(self):
        response = self.client.get(
            "/api/health", headers={"Origin": "http://127.0.0.1:5173"}
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://127.0.0.1:5173",
        )
        assert_public_payload(self, self.client.get("/openapi.json").json())

    def test_09_failed_creation_leaves_no_half_project(self):
        with patch(
            "web_backend.services.projects._create_core_checkpoint",
            side_effect=RuntimeError("simulated failure"),
        ):
            response = self.client.post("/api/projects", json=PROJECT_PAYLOAD)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(list(self.projects_root.iterdir()), [])

    def test_10_no_unimplemented_action_endpoint_is_published(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(
            set(schema["paths"]),
            {
                "/api/health",
                "/api/capabilities",
                "/api/projects",
                "/api/projects/{project_id}",
                "/api/projects/{project_id}/workflow",
                "/api/projects/{project_id}/planning/creative",
                "/api/projects/{project_id}/planning/storyboard",
                "/api/projects/{project_id}/planning/video-prompts",
                "/api/projects/{project_id}/shots",
                "/api/projects/{project_id}/shots/{shot_id}",
                "/api/projects/{project_id}/shots/{shot_id}/versions/{version}/video",
            },
        )
        self.assertNotIn("/api/actions", schema["paths"])


if __name__ == "__main__":
    unittest.main()
