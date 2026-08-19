from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
import time
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import base_project, write_json, write_project
from tests.web.web_response_assertions import assert_public_payload


def png(width: int = 2, height: int = 3, color: int = 64) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([color, color, color]) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


JPEG = (
    b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x01\x00\x01\x03"
    b"\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9"
)
WEBP = b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + b"\x00" * 14


def snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for path in root.rglob("*") if root.exists() else ():
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
    return result


class WebBackendPhase3D15ReferenceAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.locking import ProjectLockManager
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.lock_manager = ProjectLockManager()
        self.project_dir = self.write_new_project("project-a", "project-a")
        self.second_dir = self.write_new_project("project-b", "project-b")
        self.network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("provider/network call is forbidden"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            ),
            lock_manager=self.lock_manager,
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    def write_new_project(self, project_id: str, directory_name: str) -> Path:
        project = base_project(project_id=project_id, project_name=directory_name)
        project["status"] = "NOT_STARTED"
        project["current_stage"] = "CREATIVE"
        return write_project(self.projects_root, directory_name, project)

    def upload(
        self,
        name: str,
        content: bytes,
        *,
        project_id: str = "project-a",
        media_type: str = "application/octet-stream",
    ):
        return self.client.post(
            f"/api/projects/{project_id}/references",
            files={"file": (name, content, media_type)},
        )

    def assert_error(self, response, status: int, code: str) -> None:
        self.assertEqual(response.status_code, status)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], code)
        assert_public_payload(self, payload)

    def test_01_png_jpg_jpeg_and_webp_use_core_ids_and_dimensions(self):
        first = self.upload("product.png", png(4, 5), media_type="text/plain")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json(), {
            "asset_id": "ref_001",
            "filename": "ref_001.png",
            "media_type": "image/png",
            "width": 4,
            "height": 5,
            "deduplicated": False,
        })
        jpg = self.upload("photo.jpg", JPEG)
        jpeg = self.upload("same-photo.jpeg", JPEG)
        webp = self.upload("subject.webp", WEBP)
        self.assertEqual(jpg.status_code, 201)
        self.assertEqual(jpg.json()["asset_id"], "ref_002")
        self.assertEqual(jpeg.status_code, 201)
        self.assertEqual(jpeg.json()["asset_id"], "ref_002")
        self.assertTrue(jpeg.json()["deduplicated"])
        self.assertEqual(webp.status_code, 201)
        self.assertEqual(webp.json()["asset_id"], "ref_003")

    def test_02_empty_corrupt_fake_extension_unsupported_and_oversized_are_safe(self):
        self.assert_error(self.upload("empty.png", b""), 422, "INVALID_REFERENCE_FILE")
        self.assert_error(
            self.upload("corrupt.png", b"not an image"),
            422,
            "REFERENCE_IMAGE_INVALID",
        )
        self.assert_error(
            self.upload("fake.png", JPEG),
            422,
            "REFERENCE_IMAGE_INVALID",
        )
        self.assert_error(
            self.upload("unsupported.gif", b"GIF89a"),
            415,
            "UNSUPPORTED_IMAGE_FORMAT",
        )
        with patch("web_backend.services.reference_assets.MAX_IMAGE_BYTES", 16):
            self.assert_error(
                self.upload("large.png", png()),
                413,
                "REFERENCE_FILE_TOO_LARGE",
            )

    def test_03_upload_reuses_core_manager_manifest_and_sha_dedup(self):
        content = png(color=85)
        first = self.upload("one.png", content).json()
        second = self.upload("two.png", content).json()
        self.assertEqual(first["asset_id"], second["asset_id"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        manifest = json.loads(
            (self.project_dir / "references" / "reference_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(len(manifest["assets"]), 1)
        record = manifest["assets"][0]
        self.assertEqual(record["asset_id"], "ref_001")
        self.assertEqual(record["source"], "user_upload")
        self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
        self.assertNotIn("use_as_reference_asset", record)
        self.assertNotIn("use_as_first_frame", record)
        self.assertNotIn("minimax_mode", record)
        self.assertFalse((self.project_dir / "references" / "analysis").exists())
        self.assertFalse((self.project_dir / "references" / "visual_analysis").exists())

    def test_04_upload_is_project_level_and_does_not_change_workflow_or_tasks(self):
        before = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        response = self.upload("early-input.png", png())
        self.assertEqual(response.status_code, 201)
        after = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        self.assertEqual(after["current_stage"], "CREATIVE")
        self.assertEqual(after["status"], "NOT_STARTED")
        self.assertFalse((self.project_dir / "concepts").exists())
        self.assertFalse((self.project_dir / "storyboard").exists())
        self.assertFalse((self.project_dir / "shots").exists())
        self.assertEqual(
            self.application.state.task_repository.list_for_project("project-a"), []
        )
        self.assertFalse(self.runtime_root.exists())

    def test_05_upload_list_preview_share_one_stable_asset_id(self):
        created = self.upload("product.png", png()).json()
        listed = self.client.get("/api/projects/project-a/references")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [item["asset_id"] for item in listed.json()["assets"]],
            [created["asset_id"]],
        )
        preview = self.client.get(
            f"/api/projects/project-a/references/{created['asset_id']}/image"
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["content-type"], "image/png")
        self.assertEqual(preview.content, png())
        assert_public_payload(self, created)

    def test_06_filename_cannot_control_destination_or_escape_response(self):
        for name in (
            "../../outside.png",
            r"C:\private\secret.png",
            r"\\server\share\secret.png",
            "CON:bad.png",
            "a" * 300 + ".png",
        ):
            response = self.upload(name, png(color=len(name) % 200 + 1))
            self.assertEqual(response.status_code, 201)
            payload = response.json()
            self.assertRegex(payload["asset_id"], r"^ref_\d{3}$")
            self.assertRegex(payload["filename"], r"^ref_\d{3}\.png$")
            assert_public_payload(self, payload)
        self.assertFalse((self.root / "outside.png").exists())
        self.assertEqual(
            len(list((self.project_dir / "references" / "project").glob("*.png"))),
            5,
        )

    def test_07_get_and_preview_remain_zero_side_effect_after_write(self):
        created = self.upload("product.png", png()).json()
        before = snapshot(self.project_dir)
        self.assertEqual(
            self.client.get("/api/projects/project-a/references").status_code, 200
        )
        self.assertEqual(
            self.client.get(
                f"/api/projects/project-a/references/{created['asset_id']}/image"
            ).status_code,
            200,
        )
        self.assertEqual(snapshot(self.project_dir), before)

        empty_before = snapshot(self.second_dir)
        self.assertEqual(
            self.client.get("/api/projects/project-b/references").json()["assets"], []
        )
        self.assertEqual(snapshot(self.second_dir), empty_before)
        self.assertFalse((self.second_dir / "references").exists())

    def test_08_same_project_is_serialized_and_busy_is_safe(self):
        from reference_assets import ReferenceAssetManager

        entered = threading.Event()
        release = threading.Event()
        original = ReferenceAssetManager.import_image

        def blocking_import(manager, source):
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return original(manager, source)

        with patch.object(ReferenceAssetManager, "import_image", blocking_import):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.upload, "first.png", png(color=10))
                self.assertTrue(entered.wait(timeout=3))
                second = self.upload("second.png", png(color=20))
                self.assert_error(second, 409, "PROJECT_BUSY")
                release.set()
                self.assertEqual(first.result(timeout=3).status_code, 201)
        manifest = json.loads(
            (self.project_dir / "references" / "reference_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(manifest["assets"]), 1)

    def test_09_different_projects_can_upload_in_parallel(self):
        from reference_assets import ReferenceAssetManager

        barrier = threading.Barrier(2)
        original = ReferenceAssetManager.import_image

        def parallel_import(manager, source):
            barrier.wait(timeout=3)
            time.sleep(0.02)
            return original(manager, source)

        with patch.object(ReferenceAssetManager, "import_image", parallel_import):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(
                    pool.map(
                        lambda item: self.upload(
                            item[0], item[1], project_id=item[2]
                        ),
                        [
                            ("a.png", png(color=11), "project-a"),
                            ("b.png", png(color=22), "project-b"),
                        ],
                    )
                )
        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertTrue((self.project_dir / "references" / "project" / "ref_001.png").is_file())
        self.assertTrue((self.second_dir / "references" / "project" / "ref_001.png").is_file())

    def test_10_uploaded_asset_drives_real_preflight_routes_without_fallback(self):
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        for stage in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT"):
            project["stages"][stage]["status"] = "COMPLETED"
        for stage in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
            project["stages"][stage]["status"] = "APPROVED"
        project["current_stage"] = "PROMPT_REVIEW"
        project["status"] = "APPROVED"
        project["video_generation"]["shots"] = {
            "1": {
                "shot_id": 1,
                "status": "NOT_STARTED",
                "generation_count": 0,
                "active_prompt_version": 1,
                "approved_prompt_version": None,
                "active_video_version": None,
                "approved_video_version": None,
                "pending_video_version": None,
                "prompt_versions": [{"version": 1, "prompt": "approved", "source": "ai_generated"}],
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        write_json(self.project_dir / "project.json", project)
        write_json(
            self.project_dir / "storyboard" / "storyboard.json",
            {"total_duration": 6, "shots": [{"shot_id": 1, "duration": 6}]},
        )
        write_json(
            self.project_dir / "storyboard" / "video_prompts.json",
            {"shots": [{"shot_id": 1, "video_prompt": "approved"}]},
        )
        asset_id = self.upload("product.png", png()).json()["asset_id"]

        def preflight(mode: str, selection: str = "AUTO", model: str | None = None):
            return self.client.post(
                "/api/projects/project-a/shots/shot_01/generation/preflight",
                json={
                    "model_selection": selection,
                    "requested_model": model,
                    "visual_input": {"mode": mode, "asset_ids": [asset_id]},
                },
            ).json()

        with patch.dict(
            os.environ,
            {"MINIMAX_API_KEY": "mock-hailuo", "MINIMAX_H3_API_KEY": "mock-h3"},
        ):
            reference = preflight("reference_asset")
            first = preflight("first_frame")
            incompatible = preflight(
                "reference_asset", "MANUAL", "MiniMax-Hailuo-2.3"
            )
        self.assertTrue(reference["ready"])
        self.assertEqual(reference["resolved"]["model"], "MiniMax-H3")
        self.assertTrue(first["ready"])
        self.assertEqual(first["resolved"]["model"], "MiniMax-Hailuo-2.3")
        self.assertFalse(incompatible["ready"])
        self.assertEqual(
            incompatible["issues"][0]["code"],
            "MODEL_VISUAL_INPUT_INCOMPATIBLE",
        )

    def test_11_missing_project_and_openapi_are_safe(self):
        self.assert_error(
            self.upload("product.png", png(), project_id="missing"),
            404,
            "PROJECT_NOT_FOUND",
        )
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        document = schema.json()
        route = document["paths"]["/api/projects/{project_id}/references"]["post"]
        self.assertIn("multipart/form-data", route["requestBody"]["content"])
        body_schema = route["requestBody"]["content"]["multipart/form-data"]["schema"]
        component_name = body_schema["$ref"].rsplit("/", 1)[-1]
        component = document["components"]["schemas"][component_name]
        self.assertEqual(set(component["properties"]), {"file"})
        self.assertEqual(component["required"], ["file"])
        rendered = json.dumps(route, ensure_ascii=False).lower()
        for forbidden in (
            "destination",
            "project_path",
            "source_path",
            "credential",
            "analysis_status",
            "minimax_mode",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_12_manifest_failure_removes_only_the_new_asset(self):
        from project_manager import ProjectDirectoryError, ProjectPaths

        first = self.upload("existing.png", png(color=31))
        self.assertEqual(first.status_code, 201)
        existing = self.project_dir / "references" / "project" / "ref_001.png"
        before = existing.read_bytes()
        with patch.object(
            ProjectPaths,
            "save_json",
            side_effect=ProjectDirectoryError("simulated manifest failure"),
        ):
            failed = self.upload("new.png", png(color=32))
        self.assert_error(failed, 500, "REFERENCE_IMPORT_FAILED")
        self.assertEqual(existing.read_bytes(), before)
        self.assertFalse(
            (self.project_dir / "references" / "project" / "ref_002.png").exists()
        )
        manifest = json.loads(
            (self.project_dir / "references" / "reference_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["asset_id"] for item in manifest["assets"]], ["ref_001"])


if __name__ == "__main__":
    unittest.main()
