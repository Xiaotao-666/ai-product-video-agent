from __future__ import annotations

import base64
import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.web.test_backend_phase_1b_projects import (
    base_project,
    write_json,
    write_project,
)
from tests.web.web_response_assertions import assert_public_payload


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    if not root.exists():
        return {}
    result: dict[str, tuple] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        stat = path.lstat()
        if path.is_file():
            result[relative] = (
                "file",
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            result[relative] = ("dir", stat.st_mtime_ns)
    return result


class WebBackendPhase3D1GenerationPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects_root = self.root / "projects"
        self.runtime_root = self.root / "runtime"
        self.project_dir = self._write_project("中文项目", "中文项目目录")
        self.asset_id = self._write_reference(self.project_dir)
        self.environment = patch.dict(
            os.environ,
            {
                "MINIMAX_API_KEY": "mock-hailuo-key",
                "MINIMAX_H3_API_KEY": "mock-h3-key",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.network_guard = patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("real provider/network call"),
        )
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)
        self.hailuo_submit_guard = patch(
            "providers.minimax_hailuo_provider.MiniMaxHailuoProvider.submit",
            side_effect=AssertionError("MiniMax submit call"),
        )
        self.h3_submit_guard = patch(
            "providers.minimax_h3_provider.MiniMaxH3Provider.submit",
            side_effect=AssertionError("MiniMax submit call"),
        )
        self.hailuo_submit_guard.start()
        self.h3_submit_guard.start()
        self.addCleanup(self.hailuo_submit_guard.stop)
        self.addCleanup(self.h3_submit_guard.stop)
        self.application = create_app(
            settings=BackendSettings(
                projects_root=self.projects_root,
                runtime_root=self.runtime_root,
                task_workers=1,
            )
        )
        self.client = TestClient(self.application, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.application.state.task_runner.shutdown)

    def _write_project(self, project_id: str, directory_name: str) -> Path:
        project = base_project(project_id=project_id, project_name="生成准备测试")
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
                "active_prompt_version": 2,
                "approved_prompt_version": None,
                "active_video_version": None,
                "approved_video_version": None,
                "pending_video_version": None,
                "prompt_versions": [
                    {"version": 1, "prompt": "old prompt", "source": "ai_generated"},
                    {"version": 2, "prompt": "approved active prompt", "source": "ai_revision"},
                ],
                "generation_versions": [],
                "candidate": {"status": "NONE", "video_version": None},
            }
        }
        directory = write_project(self.projects_root, directory_name, project)
        write_json(
            directory / "storyboard" / "storyboard.json",
            {
                "total_duration": 6,
                "shots": [
                    {
                        "shot_id": 1,
                        "duration": 6,
                        "purpose": "product closeup",
                        "visual": "product on table",
                        "camera": "static",
                        "voiceover_cues": [],
                        "subtitle_cues": [],
                        "video_constraints": {
                            "reserve_subtitle_space": False,
                            "subtitle_safe_area": "none",
                        },
                    }
                ],
            },
        )
        write_json(
            directory / "storyboard" / "video_prompts.json",
            {
                "shots": [
                    {
                        "shot_id": 1,
                        "visual_prompt_core": "approved core",
                        "video_prompt": "approved active prompt",
                    }
                ]
            },
        )
        return directory

    @staticmethod
    def _write_reference(directory: Path) -> str:
        target = directory / "references" / "project" / "ref_001.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_PNG)
        digest = hashlib.sha256(_PNG).hexdigest()
        write_json(
            directory / "references" / "reference_manifest.json",
            {
                "version": 1,
                "assets": [
                    {
                        "asset_id": "ref_001",
                        "filename": target.name,
                        "type": "reference_image",
                        "source": "user_upload",
                        "project_path": "references/project/ref_001.png",
                        "sha256": digest,
                        "file_size": len(_PNG),
                        "width": 1,
                        "height": 1,
                    }
                ],
            },
        )
        return "ref_001"

    def options(self, project_id: str = "中文项目"):
        return self.client.get(
            f"/api/projects/{project_id}/shots/shot_01/generation/options"
        )

    def preflight(self, payload: dict, project_id: str = "中文项目"):
        return self.client.post(
            f"/api/projects/{project_id}/shots/shot_01/generation/preflight",
            json=payload,
        )

    @staticmethod
    def payload(
        mode: str = "none",
        *,
        selection: str = "AUTO",
        model: str | None = None,
        assets: list[str] | None = None,
    ) -> dict:
        return {
            "model_selection": selection,
            "requested_model": model,
            "visual_input": {"mode": mode, "asset_ids": assets or []},
        }

    def test_01_options_use_core_registry_and_show_prompt_state(self):
        response = self.options()
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["eligible"])
        self.assertEqual(payload["selection_modes"], ["AUTO", "MANUAL"])
        self.assertEqual(payload["shot"]["prompt_version"], 2)
        self.assertEqual(payload["shot"]["duration_seconds"], 6)
        self.assertEqual(payload["shot"]["resolution"], "768P")
        models = {item["model_id"]: item for item in payload["models"]}
        self.assertEqual(
            models["MiniMax-Hailuo-2.3"]["supported_visual_input_modes"],
            ["none", "first_frame"],
        )
        self.assertEqual(
            models["MiniMax-H3"]["supported_visual_input_modes"],
            ["none", "reference_asset", "first_frame"],
        )
        self.assertTrue(all(item["available"] for item in models.values()))
        assert_public_payload(self, payload)

    def test_02_reference_list_preview_empty_and_chinese_project_id(self):
        response = self.client.get("/api/projects/中文项目/references")
        self.assertEqual(response.status_code, 200)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["asset_id"], "ref_001")
        self.assertEqual(asset["media_type"], "image/png")
        self.assertEqual((asset["width"], asset["height"]), (1, 1))
        self.assertNotIn("path", json.dumps(asset).lower())
        image = self.client.get("/api/projects/中文项目/references/ref_001/image")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertEqual(image.content, _PNG)

        empty = self._write_project("empty-project", "empty-project")
        before = _tree_snapshot(empty)
        response = self.client.get("/api/projects/empty-project/references")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["assets"], [])
        self.assertEqual(_tree_snapshot(empty), before)
        self.assertFalse((empty / "references").exists())

    def test_03_none_auto_resolves_hailuo_without_assets(self):
        response = self.preflight(self.payload())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["selected_asset_ids"], [])
        self.assertEqual(payload["shot"]["prompt_version"], 2)
        self.assertEqual(payload["resolved"]["model"], "MiniMax-Hailuo-2.3")
        self.assertEqual(payload["resolved"]["api_version"], "v1")
        self.assertEqual(payload["resolved"]["generation_mode"], "text_to_video")
        self.assertTrue(payload["provider_available"])
        self.assertTrue(payload["paid_call_required"])

    def test_04_reference_and_first_frame_follow_core_routes(self):
        reference = self.preflight(
            self.payload("reference_asset", assets=[self.asset_id])
        )
        self.assertEqual(reference.status_code, 200)
        self.assertTrue(reference.json()["ready"])
        self.assertEqual(reference.json()["resolved"]["model"], "MiniMax-H3")
        self.assertEqual(reference.json()["resolved"]["api_version"], "v2")
        self.assertEqual(
            reference.json()["resolved"]["generation_mode"],
            "reference_generation",
        )

        first = self.preflight(self.payload("first_frame", assets=[self.asset_id]))
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["ready"])
        self.assertEqual(first.json()["resolved"]["model"], "MiniMax-Hailuo-2.3")
        self.assertEqual(first.json()["resolved"]["generation_mode"], "first_frame")

    def test_05_manual_selection_rejects_incompatible_and_unknown_without_fallback(self):
        incompatible = self.preflight(
            self.payload(
                "reference_asset",
                selection="MANUAL",
                model="MiniMax-Hailuo-2.3",
                assets=[self.asset_id],
            )
        )
        self.assertEqual(incompatible.status_code, 200)
        self.assertFalse(incompatible.json()["ready"])
        self.assertEqual(
            [item["code"] for item in incompatible.json()["issues"]],
            ["MODEL_VISUAL_INPUT_INCOMPATIBLE"],
        )
        self.assertIsNone(incompatible.json()["resolved"])

        unknown = self.preflight(
            self.payload("none", selection="MANUAL", model="unknown-model")
        )
        self.assertFalse(unknown.json()["ready"])
        self.assertEqual(unknown.json()["issues"][0]["code"], "MODEL_UNAVAILABLE")
        self.assertIsNone(unknown.json()["resolved"])

        compatible = self.preflight(
            self.payload("none", selection="MANUAL", model="MiniMax-H3")
        )
        self.assertTrue(compatible.json()["ready"])
        self.assertEqual(compatible.json()["resolved"]["model"], "MiniMax-H3")

    def test_06_missing_assets_and_none_with_asset_return_explicit_issues(self):
        reference = self.preflight(self.payload("reference_asset"))
        self.assertEqual(reference.json()["issues"][0]["code"], "REFERENCE_ASSET_REQUIRED")
        first = self.preflight(self.payload("first_frame"))
        self.assertEqual(first.json()["issues"][0]["code"], "FIRST_FRAME_REQUIRED")
        missing = self.preflight(
            self.payload("reference_asset", assets=["ref_999"])
        )
        self.assertEqual(missing.json()["issues"][0]["code"], "REFERENCE_ASSET_NOT_FOUND")
        none = self.preflight(self.payload("none", assets=[self.asset_id]))
        self.assertEqual(none.json()["issues"][0]["code"], "VISUAL_INPUT_ASSET_NOT_ALLOWED")
        self.assertTrue(all(not item.json()["ready"] for item in (reference, first, missing, none)))

    def test_07_provider_unavailable_never_silently_downgrades(self):
        with patch.dict(os.environ, {"MINIMAX_H3_API_KEY": ""}):
            response = self.preflight(
                self.payload("reference_asset", assets=[self.asset_id])
            )
            options = self.options().json()
        payload = response.json()
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["resolved"]["model"], "MiniMax-H3")
        self.assertEqual(payload["issues"][0]["code"], "PROVIDER_NOT_CONFIGURED")
        h3 = next(item for item in options["models"] if item["model_id"] == "MiniMax-H3")
        self.assertFalse(h3["available"])

    def test_08_prompt_and_shot_state_are_validated(self):
        project = json.loads((self.project_dir / "project.json").read_text(encoding="utf-8"))
        project["stages"]["PROMPT_REVIEW"]["status"] = "WAITING_REVIEW"
        write_json(self.project_dir / "project.json", project)
        waiting = self.preflight(self.payload()).json()
        self.assertFalse(waiting["ready"])
        self.assertIn("PROMPT_NOT_APPROVED", [item["code"] for item in waiting["issues"]])

        project["stages"]["PROMPT_REVIEW"]["status"] = "APPROVED"
        shot = project["video_generation"]["shots"]["1"]
        shot["generation_count"] = 1
        shot["generation_versions"] = [{"video_version": 1}]
        write_json(self.project_dir / "project.json", project)
        generated = self.preflight(self.payload()).json()
        self.assertFalse(generated["ready"])
        self.assertIn("SHOT_ALREADY_GENERATED", [item["code"] for item in generated["issues"]])

    def test_09_invalid_requests_and_safe_resource_errors(self):
        self.assertEqual(
            self.preflight(
                {
                    "model_selection": "MANUAL",
                    "requested_model": None,
                    "visual_input": {"mode": "none", "asset_ids": []},
                }
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/api/projects/中文项目/references/../image").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/api/projects/中文项目/references/ref_999/image").status_code,
            404,
        )
        self.assertEqual(self.options("missing-project").status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/projects/中文项目/shots/shot_999/generation/options"
            ).status_code,
            404,
        )

    def test_10_symlink_escape_is_rejected_when_supported(self):
        outside = self.root / "outside.png"
        outside.write_bytes(_PNG)
        link = self.project_dir / "references" / "project" / "ref_002.png"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        manifest = json.loads(
            (self.project_dir / "references" / "reference_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        manifest["assets"].append(
            {
                "asset_id": "ref_002",
                "filename": "ref_002.png",
                "source": "user_upload",
                "project_path": "references/project/ref_002.png",
                "sha256": hashlib.sha256(_PNG).hexdigest(),
            }
        )
        write_json(
            self.project_dir / "references" / "reference_manifest.json", manifest
        )
        response = self.client.get(
            "/api/projects/中文项目/references/ref_002/image"
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "REFERENCE_ASSET_DATA_CORRUPT")

    def test_11_all_new_endpoints_have_zero_project_or_task_side_effects(self):
        before = _tree_snapshot(self.project_dir)
        task_count = len(
            self.application.state.task_repository.list_for_project("中文项目")
        )
        calls = [
            self.options(),
            self.client.get("/api/projects/中文项目/references"),
            self.client.get("/api/projects/中文项目/references/ref_001/image"),
            self.preflight(self.payload()),
            self.preflight(self.payload("reference_asset", assets=[self.asset_id])),
        ]
        self.assertTrue(all(response.status_code == 200 for response in calls))
        self.assertEqual(_tree_snapshot(self.project_dir), before)
        self.assertEqual(
            len(self.application.state.task_repository.list_for_project("中文项目")),
            task_count,
        )
        self.assertFalse(self.runtime_root.exists())
        self.assertFalse(any(self.project_dir.rglob("generation.json")))
        self.assertFalse(any(self.project_dir.rglob("*.mp4")))

    def test_12_openapi_is_safe_and_endpoint_specific(self):
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        payload = schema.json()
        route = payload["paths"][
            "/api/projects/{project_id}/shots/{shot_id}/generation/preflight"
        ]["post"]
        example = route["responses"]["200"]["content"]["application/json"]["example"]
        self.assertEqual(example["resolved"]["model_selection"], "AUTO")
        self.assertEqual(example["resolved"]["visual_input_mode"], "none")
        rendered = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("minimax_api_key", rendered)
        self.assertNotIn("credential_env_name", rendered)
        self.assertNotIn("d:\\", rendered)

    def test_13_invalid_duration_is_an_issue_not_a_provider_call_or_500(self):
        storyboard = json.loads(
            (self.project_dir / "storyboard" / "storyboard.json").read_text(
                encoding="utf-8"
            )
        )
        storyboard["shots"][0]["duration"] = 8
        write_json(
            self.project_dir / "storyboard" / "storyboard.json", storyboard
        )
        response = self.preflight(self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ready"])
        self.assertIn(
            "INVALID_DURATION",
            [item["code"] for item in response.json()["issues"]],
        )


if __name__ == "__main__":
    unittest.main()
