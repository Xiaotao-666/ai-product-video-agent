from __future__ import annotations

import importlib
import json
import os
import re
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient


BASELINE_HEALTH = {
    "status": "ok",
    "service": "ai-product-video-agent",
    "api_version": "v1",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)[a-z]:[\\/]")


class WebBackendPhase1ATests(unittest.TestCase):
    def client_for(self, application=None) -> TestClient:
        if application is None:
            from web_backend.app import create_app

            application = create_app()
        client = TestClient(application, raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client

    def assert_safe_error(self, response, expected_status: int) -> dict:
        self.assertEqual(response.status_code, expected_status)
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(
            set(payload["error"]),
            {"type", "code", "message", "retryable", "correlation_id"},
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIsNone(WINDOWS_ABSOLUTE_PATH.search(serialized))
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("API_KEY", serialized)
        self.assertEqual(
            payload["error"]["correlation_id"],
            response.headers["X-Correlation-ID"],
        )
        return payload

    def test_01_app_import_is_lightweight_and_does_not_call_core_or_tools(self):
        blocked_modules = {
            "main",
            "project_manager",
            "project_state",
            "video_generator",
            "video_assembly",
            "export_pipeline",
        }
        before = {name: name in sys.modules for name in blocked_modules}
        for name in list(sys.modules):
            if name == "web_backend" or name.startswith("web_backend."):
                del sys.modules[name]

        with (
            patch.object(subprocess, "run") as run,
            patch.object(subprocess, "Popen") as popen,
            patch.object(requests.sessions.Session, "request") as request,
        ):
            module = importlib.import_module("web_backend.app")

        self.assertIsNotNone(module.app)
        run.assert_not_called()
        popen.assert_not_called()
        request.assert_not_called()
        self.assertEqual(before, {name: name in sys.modules for name in blocked_modules})

    def test_02_health_returns_200(self):
        response = self.client_for().get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_03_health_schema_is_exact(self):
        response = self.client_for().get("/api/health")
        self.assertEqual(response.json(), BASELINE_HEALTH)

    def test_04_health_contains_no_secret_or_absolute_path(self):
        response = self.client_for().get("/api/health")
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for forbidden in (
            "API_KEY",
            "SECRET",
            "Authorization",
            "projects_root",
            ".env",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIsNone(WINDOWS_ABSOLUTE_PATH.search(serialized))

    def test_05_response_contains_correlation_id(self):
        response = self.client_for().get("/api/health")
        self.assertRegex(response.headers["X-Correlation-ID"], r"^req_[0-9a-f]{32}$")

    def test_06_independent_requests_receive_different_ids(self):
        client = self.client_for()
        first = client.get("/api/health").headers["X-Correlation-ID"]
        second = client.get("/api/health").headers["X-Correlation-ID"]
        self.assertNotEqual(first, second)

    def test_07_safe_client_correlation_id_is_inherited(self):
        supplied = "req_browser-123.abc"
        response = self.client_for().get(
            "/api/health",
            headers={"X-Correlation-ID": supplied},
        )
        self.assertEqual(response.headers["X-Correlation-ID"], supplied)

    def test_08_unsafe_client_correlation_id_is_replaced(self):
        response = self.client_for().get(
            "/api/health",
            headers={"X-Correlation-ID": "contains spaces and unsafe data"},
        )
        self.assertRegex(response.headers["X-Correlation-ID"], r"^req_[0-9a-f]{32}$")

    def test_09_404_uses_safe_error_model(self):
        response = self.client_for().get("/api/not-found")
        payload = self.assert_safe_error(response, 404)
        self.assertEqual(payload["error"]["code"], "ROUTE_NOT_FOUND")

    def test_10_422_uses_safe_error_model(self):
        from web_backend.app import create_app

        application = create_app()

        @application.get("/_test/items/{item_id}")
        async def read_item(item_id: int) -> dict[str, int]:
            return {"item_id": item_id}

        response = self.client_for(application).get("/_test/items/not-an-int")
        payload = self.assert_safe_error(response, 422)
        self.assertEqual(payload["error"]["code"], "INVALID_REQUEST")

    def test_11_internal_exception_returns_safe_500(self):
        from web_backend.app import create_app

        application = create_app()

        @application.get("/_test/error")
        async def explode() -> None:
            raise RuntimeError(
                r"private D:\projects\customer path DEEPSEEK_API_KEY=do-not-return"
            )

        with self.assertLogs("uvicorn.error.web_errors", level="ERROR"):
            response = self.client_for(application).get("/_test/error")
        payload = self.assert_safe_error(response, 500)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["error"]["code"], "UNEXPECTED_ERROR")
        self.assertNotIn("do-not-return", serialized)
        self.assertNotIn("RuntimeError", serialized)

    def test_12_startup_does_not_create_or_modify_project_files(self):
        from web_backend.app import create_app

        with TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir) / "projects-must-not-be-created"
            with patch.dict(
                os.environ,
                {"WEB_PROJECTS_ROOT": str(projects_root)},
                clear=False,
            ):
                client = self.client_for(create_app())
                self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertFalse(projects_root.exists())
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_13_startup_and_health_do_not_use_network_or_ffmpeg(self):
        from web_backend.app import create_app

        with (
            patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network used"),
            ),
            patch.object(
                requests.sessions.Session,
                "request",
                side_effect=AssertionError("provider HTTP used"),
            ),
            patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("subprocess or FFmpeg used"),
            ),
            patch.object(
                subprocess,
                "Popen",
                side_effect=AssertionError("subprocess or FFmpeg used"),
            ),
        ):
            response = self.client_for(create_app()).get("/api/health")
        self.assertEqual(response.status_code, 200)

    def test_14_settings_defaults_are_local_and_do_not_touch_disk(self):
        from web_backend.settings import BackendSettings

        with patch.dict(
            os.environ,
            {"WEB_HOST": "", "WEB_PORT": "", "WEB_PROJECTS_ROOT": ""},
            clear=False,
        ):
            for name in ("WEB_HOST", "WEB_PORT", "WEB_PROJECTS_ROOT"):
                os.environ.pop(name, None)
            settings = BackendSettings.from_environment()
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8000)
        self.assertEqual(
            settings.projects_root,
            Path(r"D:\desktop\视频生成Agent产出"),
        )

    def test_15_settings_accept_web_specific_environment_overrides(self):
        from web_backend.settings import BackendSettings

        with TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "still-not-created"
            with patch.dict(
                os.environ,
                {
                    "WEB_HOST": "127.0.0.1",
                    "WEB_PORT": "8123",
                    "WEB_PROJECTS_ROOT": str(target),
                },
                clear=False,
            ):
                settings = BackendSettings.from_environment()
            self.assertEqual(settings.port, 8123)
            self.assertEqual(settings.projects_root, target)
            self.assertFalse(target.exists())

    def test_16_access_log_contains_metadata_but_not_sensitive_headers(self):
        marker = "authorization-secret-must-not-be-logged"
        with self.assertLogs("uvicorn.error.web_access", level="INFO") as captured:
            response = self.client_for().get(
                "/api/health?ignored=query",
                headers={
                    "Authorization": f"Bearer {marker}",
                    "Cookie": f"session={marker}",
                },
            )
        self.assertEqual(response.status_code, 200)
        record = "\n".join(captured.output)
        for field in (
            "timestamp=",
            "correlation_id=",
            "method=GET",
            "route=/api/health",
            "status_code=200",
            "duration_ms=",
        ):
            self.assertIn(field, record)
        self.assertNotIn(marker, record)
        self.assertNotIn("ignored=query", record)


if __name__ == "__main__":
    unittest.main()
