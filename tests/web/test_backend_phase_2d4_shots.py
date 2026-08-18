from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from tests.web.web_response_assertions import assert_public_payload


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


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def tree_snapshot(root: Path):
    directories = tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir())
    )
    files = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        stat_result = path.stat()
        files[path.relative_to(root).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat_result.st_mtime_ns,
            stat_result.st_size,
        )
    return directories, files


def project_payload(project_id: str | None = "shots-project") -> dict:
    stages = {
        name: {"status": "NOT_STARTED", "updated_at": None}
        for name in STAGE_NAMES
    }
    for name in ("CREATIVE", "STORYBOARD", "VIDEO_PROMPT", "COMPLETED"):
        stages[name]["status"] = "COMPLETED"
    for name in ("CREATIVE_REVIEW", "STORYBOARD_REVIEW", "PROMPT_REVIEW"):
        stages[name]["status"] = "APPROVED"
    payload = {
        "project_schema_version": 2,
        "project_name": "LEE柠檬",
        "updated_at": "2026-08-18T18:00:00+08:00",
        "status": "COMPLETED",
        "current_stage": "COMPLETED",
        "request": {"product_name": "LEE柠檬"},
        "stages": stages,
        "video_generation": {
            "shots": {
                "1": {
                    "shot_id": 1,
                    "status": "APPROVED",
                    "generation_count": 3,
                    "active_prompt_version": 2,
                    "approved_prompt_version": 2,
                    "active_video_version": 2,
                    "approved_video_version": 2,
                    "prompt_versions": [
                        {"version": 1, "source": "ai_generated", "prompt": "prompt one"},
                        {"version": 2, "source": "ai_revision", "prompt": "prompt two"},
                        {"version": 4, "source": "ai_revision", "prompt": "prompt four"},
                    ],
                    "generation_versions": [
                        {"video_version": 1, "prompt_version": 1, "status": "APPROVED"},
                        {"video_version": 2, "prompt_version": 2, "status": "APPROVED"},
                        {"video_version": 3, "prompt_version": 4, "status": "WAITING_REVIEW"},
                    ],
                    "candidate": {
                        "status": "WAITING_REVIEW",
                        "video_version": 3,
                        "prompt_version": 4,
                    },
                },
                "2": {
                    "shot_id": 2,
                    "status": "APPROVED",
                    "generation_count": 1,
                    "active_prompt_version": 1,
                    "approved_prompt_version": 1,
                    "active_video_version": 1,
                    "approved_video_version": 1,
                    "prompt_versions": [
                        {"version": 1, "source": "ai_generated", "prompt": "shot two"}
                    ],
                    "generation_versions": [
                        {"video_version": 1, "prompt_version": 1, "status": "APPROVED"}
                    ],
                    "candidate": {"status": "NONE", "video_version": None},
                },
            }
        },
        "assembly": {"status": "NOT_STARTED", "needs_update": False},
        "post_production": {"status": "NOT_STARTED", "components": {}},
    }
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def bundle_payloads(video_version: int, prompt_version: int, review: str) -> dict[str, dict]:
    return {
        "prompt.json": {
            "shot_id": 1,
            "video_version": video_version,
            "prompt_version": prompt_version,
            "prompt_source": "ai_revision" if prompt_version > 1 else "ai_generated",
            "prompt_text": f"bound prompt {prompt_version}",
            "created_at": f"2026-08-18T10:0{video_version}:00+08:00",
        },
        "safety.json": {
            "shot_id": 1,
            "video_version": video_version,
            "final_submit_prompt": f"final submitted prompt {prompt_version}",
            "provider_task_id": "must-not-escape",
        },
        "generation.json": {
            "shot_id": 1,
            "video_version": video_version,
            "prompt_version": prompt_version,
            "provider_model": "MiniMax-H3",
            "provider_task_id": "task-secret",
            "file_id": "file-secret",
            "credential_env_name": "MINIMAX_API_KEY",
            "raw_provider_response": {"path": r"D:\private\raw.json"},
            "visual_input": {"mode": "reference_asset", "assets": []},
            "status": review,
            "created_at": f"2026-08-18T10:0{video_version}:00+08:00",
        },
        "review.json": {
            "shot_id": 1,
            "video_version": video_version,
            "review_result": review,
            "review_time": f"2026-08-18T11:0{video_version}:00+08:00",
            "history": [],
        },
    }


def create_fixture(project_dir: Path, project_id: str | None = "shots-project") -> None:
    write_json(project_dir / "project.json", project_payload(project_id))
    write_json(
        project_dir / "storyboard" / "video_prompts.json",
        {
            "shots": [
                {
                    "shot_id": 1,
                    "visual_prompt_core": "official visual core",
                    "video_prompt": "official canonical prompt",
                },
                {
                    "shot_id": 2,
                    "visual_prompt_core": "shot two core",
                    "video_prompt": "shot two canonical prompt",
                },
            ]
        },
    )
    write_json(
        project_dir / "shots" / "shot_01" / "shot.json",
        {
            "shot_schema_version": 2,
            "shot_id": 1,
            "status": "APPROVED",
            "generation_count": 3,
            "active_version": 2,
            "approved_version": 2,
            "candidate_version": 3,
            "visual_input": {"mode": "reference_asset", "assets": []},
            "versions": [1, 2, 3],
        },
    )
    for video_version, prompt_version, review in (
        (1, 1, "REJECTED"),
        (2, 2, "APPROVED"),
        (3, 4, "WAITING_REVIEW"),
    ):
        version_dir = project_dir / "shots" / "shot_01" / f"v{video_version:03d}"
        for filename, payload in bundle_payloads(video_version, prompt_version, review).items():
            write_json(version_dir / filename, payload)
        (version_dir / "video.mp4").write_bytes(bytes(range(256)) * 4)

    write_json(
        project_dir / "shots" / "shot_02" / "shot.json",
        {
            "shot_schema_version": 2,
            "shot_id": 2,
            "status": "APPROVED",
            "generation_count": 1,
            "active_version": 1,
            "approved_version": 1,
            "candidate_version": None,
            "versions": [1],
        },
    )
    shot_two = bundle_payloads(1, 1, "APPROVED")
    for payload in shot_two.values():
        payload["shot_id"] = 2
    version_dir = project_dir / "shots" / "shot_02" / "v001"
    for filename, payload in shot_two.items():
        write_json(version_dir / filename, payload)
    (version_dir / "video.mp4").write_bytes(b"shot-two-video")


class WebBackendPhase2D4ShotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.project_dir = self.projects_root / "柠檬"
        create_fixture(self.project_dir)
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        self.client = TestClient(
            create_app(settings=BackendSettings(projects_root=self.projects_root)),
            raise_server_exceptions=False,
        )
        self.addCleanup(self.client.close)

    def shot_list(self, project_id: str = "shots-project"):
        return self.client.get(f"/api/projects/{project_id}/shots")

    def shot_detail(self, shot_id: str = "shot_01", project_id: str = "shots-project"):
        return self.client.get(f"/api/projects/{project_id}/shots/{shot_id}")

    def video(self, version: str = "2", shot_id: str = "shot_01"):
        return self.client.get(
            f"/api/projects/shots-project/shots/{shot_id}/versions/{version}/video"
        )

    def version(self, number: int) -> dict:
        versions = self.shot_detail().json()["versions"]
        return next(item for item in versions if item["version"] == number)

    def test_01_shot_list_is_correct(self):
        response = self.shot_list()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["shot_id"] for item in response.json()["shots"]], ["shot_01", "shot_02"])

    def test_02_official_version_uses_core_pointer(self):
        self.assertEqual(self.shot_list().json()["shots"][0]["official_version"], 2)
        self.assertEqual(self.version(2)["role"], "OFFICIAL")

    def test_03_pending_review_is_explicit(self):
        self.assertEqual(self.shot_list().json()["shots"][0]["pending_review_version"], 3)
        self.assertEqual(self.version(3)["role"], "PENDING_REVIEW")

    def test_04_other_versions_are_history(self):
        self.assertEqual(self.version(1)["role"], "HISTORY")

    def test_05_internal_candidate_term_never_escapes(self):
        serialized = json.dumps(self.shot_detail().json(), ensure_ascii=False).casefold()
        self.assertNotIn("candidate", serialized)

    def test_06_rejected_history_is_retained(self):
        self.assertEqual(self.version(1)["review_status"], "REJECTED")

    def test_07_prompt_is_bound_to_its_video_version(self):
        pending = self.version(3)
        self.assertEqual(pending["version"], 3)
        self.assertEqual(pending["prompt"]["version"], 4)
        self.assertEqual(pending["prompt"]["final_prompt"], "final submitted prompt 4")

    def test_08_prompt_version_is_projected(self):
        self.assertEqual(self.version(2)["prompt"]["version"], 2)

    def test_09_model_is_safe_and_useful(self):
        self.assertEqual(self.version(2)["generation"]["model"], "MiniMax-H3")

    def test_10_visual_input_is_safe_and_useful(self):
        self.assertEqual(
            self.version(2)["generation"]["visual_input_mode"], "REFERENCE_ASSET"
        )

    def test_11_provider_task_id_is_not_returned(self):
        self.assertNotIn("task-secret", json.dumps(self.shot_detail().json()))
        self.assertNotIn("provider_task_id", json.dumps(self.shot_detail().json()))

    def test_12_file_id_is_not_returned(self):
        self.assertNotIn("file_id", json.dumps(self.shot_detail().json()))

    def test_13_credentials_and_paths_are_not_returned(self):
        payload = self.shot_detail().json()
        assert_public_payload(self, payload)
        self.assertNotIn("MINIMAX", json.dumps(payload))

    def test_14_project_not_found(self):
        response = self.shot_list("missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_NOT_FOUND")

    def test_15_shot_not_found(self):
        response = self.shot_detail("shot_99")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "SHOT_NOT_FOUND")

    def test_16_corrupt_shot_json_is_safe(self):
        (self.project_dir / "shots" / "shot_01" / "shot.json").write_text("{broken", encoding="utf-8")
        response = self.shot_detail()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "SHOT_DATA_CORRUPT")
        self.assertNotIn("broken", response.text)

    def test_17_missing_video_keeps_metadata_and_returns_safe_404(self):
        (self.project_dir / "shots" / "shot_01" / "v001" / "video.mp4").unlink()
        self.assertFalse(self.version(1)["video_available"])
        response = self.video("1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "VIDEO_NOT_FOUND")

    def test_18_chinese_legacy_project_id(self):
        legacy_dir = self.projects_root / "中文旧项目"
        create_fixture(legacy_dir, project_id=None)
        response = self.shot_detail(project_id="中文旧项目")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "中文旧项目")

    def test_19_encoded_shot_traversal_is_rejected(self):
        response = self.shot_detail("%252e%252e")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_SHOT_ID")

    def test_20_version_input_is_strict(self):
        for version in ("0", "01", "%252e%252e"):
            response = self.video(version)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "INVALID_SHOT_VERSION")

    def test_21_media_get_returns_mp4(self):
        response = self.video("2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(len(response.content), 1024)

    def test_22_media_range_is_supported(self):
        response = self.client.get(
            "/api/projects/shots-project/shots/shot_01/versions/2/video",
            headers={"Range": "bytes=0-31"},
        )
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["content-range"], "bytes 0-31/1024")
        self.assertEqual(response.headers["content-length"], "32")

    def test_23_media_headers_expose_no_path(self):
        response = self.video("2")
        serialized = json.dumps(dict(response.headers)).casefold()
        self.assertNotIn("content-disposition", serialized)
        self.assertNotIn("d:\\", serialized)
        self.assertNotIn("file://", serialized)

    def test_24_metadata_get_does_not_modify_shot_json(self):
        path = self.project_dir / "shots" / "shot_01" / "shot.json"
        before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        self.shot_detail()
        after = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        self.assertEqual(after, before)

    def test_25_media_get_does_not_modify_bundle(self):
        version_dir = self.project_dir / "shots" / "shot_01" / "v002"
        before = tree_snapshot(version_dir)
        self.video("2")
        self.assertEqual(tree_snapshot(version_dir), before)

    def test_26_all_gets_keep_tree_identical(self):
        before = tree_snapshot(self.project_dir)
        self.shot_list()
        self.shot_detail()
        self.video("2")
        self.assertEqual(tree_snapshot(self.project_dir), before)

    def test_27_reads_never_call_provider_or_network(self):
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError):
            self.assertEqual(self.shot_list().status_code, 200)
            self.assertEqual(self.shot_detail().status_code, 200)
            self.assertEqual(self.video("2").status_code, 200)

    def test_28_reads_never_run_ffmpeg_or_subprocess(self):
        with (
            patch.object(subprocess, "run", side_effect=AssertionError),
            patch.object(subprocess, "Popen", side_effect=AssertionError),
        ):
            self.assertEqual(self.shot_list().status_code, 200)
            self.assertEqual(self.shot_detail().status_code, 200)
            self.assertEqual(self.video("2").status_code, 200)


if __name__ == "__main__":
    unittest.main()
