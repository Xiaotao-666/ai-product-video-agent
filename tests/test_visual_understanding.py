from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardShot,
    generate_creative_brief,
    generate_storyboard,
    generate_video_prompts,
)
from task_logger import TaskLogger
from visual_understanding import VisualUnderstandingLayer
from vision_provider import (
    VisualAnalysis,
    VisionAnalysisRequest,
    VisionProvider,
    VisionProviderCapabilities,
)
from vision_provider_registry import VisionProviderRegistry
from providers.gemini_vision_provider import GeminiVisionProvider


def write_png(path: Path, width: int = 64, height: int = 64) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([30, 80, 160]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class MockVisionProvider(VisionProvider):
    provider_name = "mock-vision"
    model_name = "mock-vl-1"
    api_version = "mock-v1"
    capabilities = VisionProviderCapabilities(frozenset({"png", "jpeg", "webp"}))

    def __init__(self) -> None:
        self.calls: list[VisionAnalysisRequest] = []

    def analyze_image(self, request: VisionAnalysisRequest) -> VisualAnalysis:
        self.preflight(request)
        self.calls.append(request)
        return VisualAnalysis(
            product_identity="蓝色玻璃瓶护肤产品",
            brand_style="克制、清透、高级",
            visual_features=["圆柱瓶身", "银色泵头"],
            materials=["蓝色玻璃", "金属"],
            colors=["深蓝", "银色"],
            composition="产品居中，留白充足",
            must_keep_elements=["蓝色瓶身", "银色泵头"],
            avoid_elements=["人物", "暖黄色背景"],
        )


class VisualUnderstandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.paths = create_project_paths(self.base / "project")
        self.logger = TaskLogger(self.paths, "vision-test")
        self.assets = ReferenceAssetManager(self.paths, self.logger)
        source = self.base / "product.png"
        write_png(source)
        self.asset = self.assets.import_image(source)
        self.request = ProductVideoRequest(
            product_name="蓝瓶精华",
            product_description="补水护肤产品",
            user_notes="不要人物，镜头缓慢，保持高级克制",
            duration_seconds=6,
            video_style="高级产品广告",
            video_purpose="品牌宣传",
        )
        self.provider = MockVisionProvider()
        self.registry = VisionProviderRegistry(
            {
                "default_provider": "mock-vision",
                "providers": {"mock-vision": {}},
            }
        )
        self.registry.register(self.provider)
        self.layer = VisualUnderstandingLayer(
            self.paths, self.registry, self.logger
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_A_image_input_and_structured_json_are_cached(self) -> None:
        result = self.layer.analyze_project_references(self.assets, self.request)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0].asset_id, "ref_001")
        self.assertEqual(self.provider.calls[0].user_notes, self.request.user_notes)
        self.assertTrue(self.provider.calls[0].image_path.is_file())
        self.assertEqual(result[0]["analysis"]["colors"], ["深蓝", "银色"])
        cache = self.paths.visual_analysis_path("ref_001")
        self.assertTrue(cache.is_file())
        saved = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(saved["asset_sha256"], self.asset["sha256"])
        self.assertEqual(saved["analysis"]["must_keep_elements"], ["蓝色瓶身", "银色泵头"])

    def test_B_resume_uses_sha_cache_without_vision_api(self) -> None:
        first = self.layer.analyze_project_references(self.assets, self.request)
        resumed_provider = MockVisionProvider()
        resumed_registry = VisionProviderRegistry(
            {"default_provider": "mock-vision", "providers": {"mock-vision": {}}}
        )
        resumed_registry.register(resumed_provider)
        resumed = VisualUnderstandingLayer(
            self.paths, resumed_registry, TaskLogger(self.paths, "vision-resume")
        ).analyze_project_references(self.assets, self.request)
        self.assertEqual(first, resumed)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(resumed_provider.calls), 0)

    def test_C_planning_prompts_receive_notes_and_reference_presence_only(self) -> None:
        visual = self.layer.analyze_project_references(self.assets, self.request)
        reference_context = {"available": True, "asset_count": 1, "asset_ids": ["ref_001"]}
        captured: dict[str, str] = {}

        def fake_request(_key, _system, user, **kwargs):
            stage = str(kwargs.get("raw_stage"))
            captured[stage] = user
            if stage == "creative":
                return {
                    "creative_concept": "c",
                    "target_audience": "a",
                    "key_message": "k",
                    "visual_direction": "v",
                    "narrative_arc": "n",
                }
            if stage == "storyboard":
                return {
                    "total_duration": 6,
                    "shots": [
                        {
                            "shot_id": 1,
                            "duration": 6,
                            "purpose": "p",
                            "visual": "v",
                            "camera": "c",
                            "voiceover_cues": [],
                            "subtitle_cues": [],
                            "video_constraints": {
                                "reserve_subtitle_space": False,
                                "subtitle_safe_area": "none",
                            },
                        }
                    ],
                }
            return {"visual_prompt_core": "prompt"}

        with patch("storyboard.deepseek_json_request", side_effect=fake_request):
            brief = generate_creative_brief(
                self.request,
                "mock",
                visual_analysis_result=visual,
                reference_asset_context=reference_context,
            )
            board = generate_storyboard(
                self.request,
                brief,
                "mock",
                visual_analysis_result=visual,
                reference_asset_context=reference_context,
            )
            generate_video_prompts(
                self.request,
                brief,
                board,
                "mock",
                visual_analysis_result=visual,
                reference_asset_context=reference_context,
            )

        for stage in ("creative", "storyboard"):
            self.assertIn("不要人物", captured[stage])
            self.assertIn("已提供 1 张参考素材", captured[stage])
            self.assertNotIn("蓝色玻璃瓶护肤产品", captured[stage])
            self.assertNotIn("银色泵头", captured[stage])
        video_stage = captured["video_prompt_shot_01"]
        self.assertIn("不要人物", video_stage)
        self.assertIn("A project reference asset is available", video_stage)
        self.assertNotIn("蓝色玻璃瓶护肤产品", video_stage)
        self.assertNotIn("银色泵头", video_stage)

    def test_D_user_notes_are_saved_to_project_json(self) -> None:
        ProjectCheckpoint.create(
            self.paths, self.request.product_name, self.request.model_dump()
        )
        payload = json.loads(
            self.paths.project_state_path().read_text(encoding="utf-8")
        )
        self.assertEqual(payload["request"]["user_notes"], self.request.user_notes)

    def test_E_old_schema2_request_backfills_empty_user_notes(self) -> None:
        checkpoint = ProjectCheckpoint.create(
            self.paths, self.request.product_name, self.request.model_dump()
        )
        checkpoint.data["request"].pop("user_notes")
        self.paths.save_json(self.paths.project_state_path(), checkpoint.data)
        resumed = ProjectCheckpoint.load(self.paths)
        self.assertEqual(resumed.data["request"]["user_notes"], "")
        validated = ProductVideoRequest.model_validate(resumed.data["request"])
        self.assertEqual(validated.user_notes, "")

    def test_F_cached_results_can_be_loaded_without_registered_provider(self) -> None:
        expected = self.layer.analyze_project_references(self.assets, self.request)
        empty_registry = VisionProviderRegistry(
            {"default_provider": None, "providers": {}}
        )
        actual = VisualUnderstandingLayer(
            self.paths, empty_registry
        ).cached_project_results(self.assets)
        self.assertEqual(actual, expected)

    def test_G_gemini_adapter_maps_image_and_structured_json_without_real_api(self) -> None:
        adapter = GeminiVisionProvider(
            api_key="mock-gemini-key",
            model_name="gemini-test-vision",
            api_version="v1beta",
            credential_env_name="GEMINI_API_KEY",
            supported_image_formats=frozenset({"png"}),
        )
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    self.provider.analyze_image(
                                        VisionAnalysisRequest(
                                            image_path=self.assets.asset_path("ref_001"),
                                            asset_id="ref_001",
                                            asset_sha256=self.asset["sha256"],
                                            image_format="png",
                                            product_name="P",
                                            product_description="D",
                                            user_notes="N",
                                        )
                                    ).model_dump(),
                                    ensure_ascii=False,
                                )
                            }
                        ]
                    }
                }
            ]
        }
        request = VisionAnalysisRequest(
            image_path=self.assets.asset_path("ref_001"),
            asset_id="ref_001",
            asset_sha256=self.asset["sha256"],
            image_format="png",
            product_name=self.request.product_name,
            product_description=self.request.product_description,
            user_notes=self.request.user_notes,
        )
        with patch("providers.gemini_vision_provider.requests.post", return_value=response) as post:
            result = adapter.analyze_image(request)
        self.assertEqual(result.product_identity, "蓝色玻璃瓶护肤产品")
        sent = post.call_args.kwargs
        self.assertIn("gemini-test-vision:generateContent", post.call_args.args[0])
        self.assertEqual(sent["headers"]["x-goog-api-key"], "mock-gemini-key")
        parts = sent["json"]["contents"][0]["parts"]
        self.assertTrue(parts[1]["inlineData"]["data"])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(
            sent["json"]["generationConfig"]["responseMimeType"],
            "application/json",
        )

    def test_H_missing_vision_key_is_blocked_before_http(self) -> None:
        adapter = GeminiVisionProvider(
            api_key="",
            model_name="gemini-test-vision",
            api_version="v1beta",
            credential_env_name="GEMINI_API_KEY",
            supported_image_formats=frozenset({"png"}),
        )
        request = VisionAnalysisRequest(
            image_path=self.assets.asset_path("ref_001"),
            asset_id="ref_001",
            asset_sha256=self.asset["sha256"],
            image_format="png",
            product_name="P",
            product_description="D",
        )
        with patch("providers.gemini_vision_provider.requests.post") as post:
            with self.assertRaisesRegex(Exception, "GEMINI_API_KEY"):
                adapter.analyze_image(request)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
