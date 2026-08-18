from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import main
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager
from storyboard import generate_creative_brief
from vision_provider_registry import load_vision_provider_config


def write_png(path: Path, width: int = 24, height: int = 24) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([200, 40, 20]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class VisualUnderstandingDisabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.paths = create_project_paths(self.base / "project")
        self.manager = ReferenceAssetManager(self.paths)
        source = self.base / "reference.png"
        write_png(source)
        self.asset = self.manager.import_image(source)
        self.request = ProductVideoRequest(
            product_name="薯条",
            product_description="金黄色薯条产品",
            user_notes="不要人物，保持简洁",
            duration_seconds=6,
            video_style="高级商业广告",
            video_purpose="品牌宣传",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_A_default_config_has_no_active_vision_provider(self) -> None:
        config = load_vision_provider_config()
        self.assertFalse(config["visual_understanding_enabled"])
        self.assertIsNone(config["default_provider"])
        self.assertEqual(config["providers"], {})

    def test_B_reference_asset_is_preserved_without_analysis_file(self) -> None:
        manifest = json.loads(
            self.paths.reference_manifest_path().read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["assets"][0]["asset_id"], "ref_001")
        self.assertEqual(manifest["assets"][0]["sha256"], self.asset["sha256"])
        self.assertTrue(self.manager.asset_path("ref_001").is_file())
        self.assertEqual(list(self.paths.visual_analysis_dir.rglob("*.json")), [])

    def test_C_prompt_uses_notes_and_presence_without_image_analysis(self) -> None:
        captured = {}

        def fake_request(_key, _system, user, **_kwargs):
            captured["user"] = user
            return {
                "creative_concept": "c",
                "target_audience": "a",
                "key_message": "k",
                "visual_direction": "v",
                "narrative_arc": "n",
            }

        with patch("storyboard.deepseek_json_request", side_effect=fake_request):
            generate_creative_brief(
                self.request,
                "mock",
                reference_asset_context={
                    "available": True,
                    "asset_count": 1,
                    "asset_ids": ["ref_001"],
                },
            )
        self.assertIn("不要人物", captured["user"])
        self.assertIn("已提供 1 张参考素材", captured["user"])
        self.assertIn("不要推断或描述图片内容", captured["user"])
        self.assertNotIn("visual_analysis", captured["user"])

    def test_D_resume_keeps_asset_and_does_not_create_analysis(self) -> None:
        checkpoint = ProjectCheckpoint.create(
            self.paths, self.request.product_name, self.request.model_dump()
        )
        resumed = ProjectCheckpoint.load(self.paths)
        self.assertEqual(resumed.data["request"]["user_notes"], self.request.user_notes)
        self.assertEqual(self.manager.list_assets()[0]["asset_id"], "ref_001")
        self.assertEqual(list(self.paths.visual_analysis_dir.rglob("*.json")), [])

    def test_E_main_runtime_has_no_vision_bootstrap(self) -> None:
        self.assertFalse(hasattr(main, "VisualUnderstandingLayer"))
        self.assertFalse(hasattr(main, "VisualAnalysisReviewManager"))
        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertNotIn("analyze_project_references", source)
        self.assertNotIn("load_vision_credentials_from_env", source)


if __name__ == "__main__":
    unittest.main()
