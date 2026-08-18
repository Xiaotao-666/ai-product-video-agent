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

from web_response_assertions import assert_public_payload


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


def project_payload(project_id: str | None = "planning-project") -> dict:
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
        "updated_at": "2026-08-18T16:00:00+08:00",
        "status": "COMPLETED",
        "current_stage": "COMPLETED",
        "request": {"product_name": "LEE柠檬"},
        "stages": stages,
        "video_generation": {
            "shots": {
                "1": {
                    "shot_id": 1,
                    "status": "APPROVED",
                    "active_prompt_version": 2,
                    "approved_prompt_version": 2,
                    "prompt_versions": [
                        {
                            "version": 1,
                            "source": "ai_generated",
                            "prompt": "initial final prompt",
                        },
                        {
                            "version": 2,
                            "source": "ai_revision",
                            "prompt": "approved final prompt v2",
                            "provider_task_id": "must-not-escape",
                        },
                    ],
                },
                "2": {
                    "shot_id": 2,
                    "status": "APPROVED",
                    "active_prompt_version": 1,
                    "approved_prompt_version": 1,
                    "prompt_versions": [
                        {
                            "version": 1,
                            "source": "ai_generated",
                            "prompt": "shot two final prompt",
                        }
                    ],
                },
            }
        },
        "assembly": {"status": "NOT_STARTED", "needs_update": False},
        "post_production": {"status": "NOT_STARTED", "components": {}},
    }
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def creative_payload() -> dict:
    return {
        "creative_concept": "明亮柠檬世界",
        "target_audience": "年轻消费者",
        "key_message": "新鲜看得见",
        "visual_direction": "高明度黄色插画",
        "narrative_arc": "从清新开场到品牌收束",
        "narration_plan": {
            "enabled": True,
            "tone": "年轻活泼",
            "full_script": "新鲜看得见，酸甜刚刚好。",
            "target_duration_seconds": 12,
        },
        "subtitle_strategy": {
            "enabled": True,
            "tone": "简洁明快",
            "density": "low",
            "max_lines": 1,
            "preferred_position": "bottom_center",
            "principles": ["不遮挡产品"],
        },
        "global_constraints": {"must": [], "must_not": ["people"]},
        "av_timeline_constraints": {
            "forbidden_windows": [
                {"start": 0, "end": 3, "tracks": ["voiceover"]}
            ]
        },
    }


def storyboard_payload() -> dict:
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "建立视觉基调",
                "visual": "黄色背景与柠檬轮廓",
                "camera": "平稳推近",
                "voiceover_cues": [
                    {"text": "新鲜看得见", "start_offset": 1, "end_offset": 3}
                ],
                "subtitle_cues": [
                    {
                        "text": "LEE柠檬",
                        "start_offset": 2,
                        "end_offset": 4,
                        "position": "bottom_center",
                    }
                ],
                "video_constraints": {
                    "reserve_subtitle_space": True,
                    "subtitle_safe_area": "bottom_center",
                },
            },
            {
                "shot_id": 2,
                "duration": 6,
                "purpose": "品牌收束",
                "visual": "柠檬轻微跳动",
                "camera": "固定镜头",
                "voiceover_cues": [],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            },
        ],
    }


def video_prompts_payload() -> dict:
    return {
        "shots": [
            {
                "shot_id": 1,
                "visual_prompt_core": "bright lemon core",
                "video_prompt": "canonical prompt one",
            },
            {
                "shot_id": 2,
                "visual_prompt_core": "closing lemon core",
                "video_prompt": "canonical prompt two",
            },
        ]
    }


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


class WebBackendPhase2D3PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects_root = Path(self.temp.name) / "projects"
        self.project_dir = self.projects_root / "柠檬"
        write_json(self.project_dir / "project.json", project_payload())
        write_json(
            self.project_dir / "concepts" / "creative_brief.json", creative_payload()
        )
        write_json(
            self.project_dir / "storyboard" / "storyboard.json", storyboard_payload()
        )
        write_json(
            self.project_dir / "storyboard" / "video_prompts.json",
            video_prompts_payload(),
        )

    def client(self) -> TestClient:
        from web_backend.app import create_app
        from web_backend.settings import BackendSettings

        return TestClient(
            create_app(settings=BackendSettings(projects_root=self.projects_root))
        )

    def get(self, suffix: str, project_id: str = "planning-project"):
        return self.client().get(f"/api/projects/{project_id}/planning/{suffix}")

    def test_01_creative_existing_content_is_projected(self):
        response = self.get("creative")
        self.assertEqual(response.status_code, 200)
        content = response.json()["content"]
        self.assertEqual(content["creative_concept"], "明亮柠檬世界")
        self.assertEqual(content["target_audience"], "年轻消费者")

    def test_02_creative_not_started_is_safe(self):
        (self.project_dir / "concepts" / "creative_brief.json").unlink()
        project = project_payload()
        project["stages"]["CREATIVE"]["status"] = "NOT_STARTED"
        project["stages"]["CREATIVE_REVIEW"]["status"] = "NOT_STARTED"
        write_json(self.project_dir / "project.json", project)
        response = self.get("creative")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "NOT_STARTED")
        self.assertIsNone(response.json()["content"])

    def test_03_creative_narration_and_subtitle_strategy_are_real(self):
        content = self.get("creative").json()["content"]
        self.assertEqual(content["narration_plan"]["target_duration_seconds"], 12)
        self.assertEqual(content["subtitle_strategy"]["principles"], ["不遮挡产品"])

    def test_04_storyboard_shots_are_projected(self):
        content = self.get("storyboard").json()["content"]
        self.assertEqual(content["total_duration_seconds"], 12)
        self.assertEqual([shot["shot_id"] for shot in content["shots"]], [1, 2])

    def test_05_storyboard_cues_keep_persisted_offsets(self):
        shot = self.get("storyboard").json()["content"]["shots"][0]
        self.assertEqual(shot["voiceover_cues"][0]["start_offset"], 1)
        self.assertEqual(shot["subtitle_cues"][0]["end_offset"], 4)
        self.assertEqual(shot["subtitle_cues"][0]["position"], "bottom_center")

    def test_06_storyboard_video_constraints_are_projected(self):
        shot = self.get("storyboard").json()["content"]["shots"][0]
        self.assertEqual(
            shot["video_constraints"],
            {"reserve_subtitle_space": True, "subtitle_safe_area": "bottom_center"},
        )

    def test_07_video_prompts_are_ordered_by_canonical_plan(self):
        shots = self.get("video-prompts").json()["content"]["shots"]
        self.assertEqual([shot["shot_id"] for shot in shots], [1, 2])

    def test_08_prompt_version_uses_approved_pointer(self):
        shot = self.get("video-prompts").json()["content"]["shots"][0]
        self.assertEqual(shot["prompt_version"], 2)
        self.assertEqual(shot["prompt_source"], "ai_revision")

    def test_09_visual_core_and_official_final_prompt_are_distinct(self):
        shot = self.get("video-prompts").json()["content"]["shots"][0]
        self.assertEqual(shot["visual_prompt_core"], "bright lemon core")
        self.assertEqual(shot["prompt_text"], "approved final prompt v2")

    def test_10_legacy_project_without_prompt_pointers_uses_canonical_prompt(self):
        project = project_payload()
        project["video_generation"] = {"shots": {}}
        write_json(self.project_dir / "project.json", project)
        shot = self.get("video-prompts").json()["content"]["shots"][0]
        self.assertIsNone(shot["prompt_version"])
        self.assertEqual(shot["prompt_text"], "canonical prompt one")

    def test_11_missing_fields_use_null_and_empty_fallbacks(self):
        write_json(
            self.project_dir / "concepts" / "creative_brief.json",
            {"narration_plan": {}, "subtitle_strategy": {}},
        )
        content = self.get("creative").json()["content"]
        self.assertIsNone(content["creative_concept"])
        self.assertEqual(content["subtitle_strategy"]["principles"], [])
        self.assertEqual(content["global_constraints"], {"must": [], "must_not": []})

    def test_12_corrupt_planning_json_returns_safe_error(self):
        path = self.project_dir / "storyboard" / "storyboard.json"
        path.write_text("{broken", encoding="utf-8")
        response = self.get("storyboard")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_DATA_CORRUPT")
        self.assertNotIn("broken", response.text)

    def test_13_project_not_found(self):
        response = self.get("creative", "missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_NOT_FOUND")

    def test_14_chinese_legacy_project_id(self):
        project = project_payload(project_id=None)
        write_json(self.projects_root / "中文项目" / "project.json", project)
        write_json(
            self.projects_root / "中文项目" / "concepts" / "creative_brief.json",
            creative_payload(),
        )
        response = self.get("creative", "中文项目")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project_id"], "中文项目")

    def test_15_encoded_path_traversal_is_rejected(self):
        response = self.get("creative", "%252e%252e")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_PROJECT_ID")

    def test_16_responses_have_no_absolute_path_or_credentials(self):
        unsafe = creative_payload()
        unsafe["debug_path"] = r"D:\private\raw.json"
        unsafe["credential_env_name"] = "MINIMAX_API_KEY"
        unsafe["visual_direction"] = r"file://D:/private/raw.txt"
        write_json(self.project_dir / "concepts" / "creative_brief.json", unsafe)
        payload = self.get("creative").json()
        assert_public_payload(self, payload)
        self.assertEqual(payload["content"]["visual_direction"], "[敏感内容已隐藏]")

    def test_17_provider_task_and_file_ids_never_escape(self):
        payload = self.get("video-prompts").json()
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        self.assertNotIn("provider_task_id", serialized)
        self.assertNotIn("must-not-escape", serialized)
        self.assertNotIn("file_id", serialized)

    def test_18_all_three_gets_are_zero_side_effect(self):
        before = tree_snapshot(self.projects_root)
        client = self.client()
        for suffix in ("creative", "storyboard", "video-prompts"):
            self.assertEqual(
                client.get(f"/api/projects/planning-project/planning/{suffix}").status_code,
                200,
            )
        self.assertEqual(tree_snapshot(self.projects_root), before)

    def test_19_reads_never_call_provider_or_network(self):
        with patch.object(
            requests.sessions.Session, "request", side_effect=AssertionError
        ):
            for suffix in ("creative", "storyboard", "video-prompts"):
                self.assertEqual(self.get(suffix).status_code, 200)

    def test_20_reads_never_run_ffmpeg_or_subprocess(self):
        with (
            patch.object(subprocess, "run", side_effect=AssertionError),
            patch.object(subprocess, "Popen", side_effect=AssertionError),
        ):
            for suffix in ("creative", "storyboard", "video-prompts"):
                self.assertEqual(self.get(suffix).status_code, 200)

    def test_21_missing_storyboard_and_video_prompts_return_null(self):
        (self.project_dir / "storyboard" / "storyboard.json").unlink()
        (self.project_dir / "storyboard" / "video_prompts.json").unlink()
        self.assertIsNone(self.get("storyboard").json()["content"])
        self.assertIsNone(self.get("video-prompts").json()["content"])
