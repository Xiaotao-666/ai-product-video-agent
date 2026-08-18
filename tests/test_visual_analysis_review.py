from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from evaluation import EvaluationRecorder
from main import _visual_kwargs
from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager
from review_manager import TaskCancelled
from shot_storage import write_generation_snapshot
from storyboard import generate_creative_brief
from task_logger import TaskLogger
from visual_analysis_review import VisualAnalysisReviewManager
from visual_understanding import VisualUnderstandingLayer
from vision_provider import (
    VisualAnalysis,
    VisionAnalysisRequest,
    VisionProvider,
    VisionProviderCapabilities,
)
from vision_provider_registry import (
    VisionProviderRegistry,
    visual_understanding_enabled,
)


def write_png(path: Path, width: int = 32, height: int = 32) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes([20, 60, 180]) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


class CountingVisionProvider(VisionProvider):
    provider_name = "mock-vision"
    model_name = "mock-vl"
    api_version = "mock-v1"
    capabilities = VisionProviderCapabilities(frozenset({"png"}))

    def __init__(self) -> None:
        self.calls = 0

    def analyze_image(self, request: VisionAnalysisRequest) -> VisualAnalysis:
        self.preflight(request)
        self.calls += 1
        return VisualAnalysis(
            product_identity=f"蓝色产品-{self.calls}",
            brand_style="克制高级",
            visual_features=["圆柱瓶身"],
            materials=["玻璃", "金属"],
            colors=["深蓝", "银色"],
            composition="产品居中",
            must_keep_elements=["瓶身形状", "银色泵头"],
            avoid_elements=["人物", "Logo 变形"],
        )


class VisualAnalysisReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.paths = create_project_paths(self.base / "project")
        self.logger = TaskLogger(self.paths, "review-test")
        self.manager = ReferenceAssetManager(self.paths, self.logger)
        image = self.base / "product.png"
        write_png(image)
        self.manager.import_image(image)
        self.request = ProductVideoRequest(
            product_name="蓝瓶精华",
            product_description="补水产品",
            user_notes="不要人物",
            duration_seconds=6,
            video_style="高级广告",
            video_purpose="品牌宣传",
        )
        self.provider = CountingVisionProvider()
        self.registry = VisionProviderRegistry(
            {"default_provider": "mock-vision", "providers": {"mock-vision": {}}}
        )
        self.registry.register(self.provider)
        self.evaluation = EvaluationRecorder(self.paths)
        self.layer = VisualUnderstandingLayer(
            self.paths, self.registry, self.logger, self.evaluation
        )
        self.reviewer = VisualAnalysisReviewManager(self.paths, self.logger)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _analysis(self) -> list[dict]:
        return self.layer.analyze_project_references(self.manager, self.request)

    def test_A_approved_review_is_reused_without_vision_api(self) -> None:
        analysis = self._analysis()
        approved, constraints = self.reviewer.review(
            analysis,
            reanalyze=lambda: self.layer.analyze_project_references(
                self.manager, self.request, force_refresh=True
            ),
            input_fn=lambda _prompt: "1",
            output=lambda _text: None,
        )
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(approved[0]["analysis"]["product_identity"], "蓝色产品-1")
        self.assertIn("瓶身形状", constraints["must_preserve"])

        resumed = self.layer.analyze_project_references(self.manager, self.request)
        reused = self.reviewer.review(
            resumed,
            reanalyze=lambda: self.fail("Resume must not call Vision Provider"),
            input_fn=lambda _prompt: self.fail("Approved review must not prompt again"),
            output=lambda _text: None,
        )
        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(reused, (approved, constraints))

    def test_B_edit_creates_edited_approved_result(self) -> None:
        answers = iter(
            [
                "2",
                "人工确认的蓝色精华瓶",
                "",
                "",
                "",
                "",
                "深蓝,白色",
                "瓶身形状,Logo位置",
                "人物,改变包装设计",
            ]
        )
        approved, constraints = self.reviewer.review(
            self._analysis(),
            reanalyze=lambda: [],
            input_fn=lambda _prompt: next(answers),
            output=lambda _text: None,
        )
        saved = json.loads(
            self.paths.visual_analysis_review_path().read_text(encoding="utf-8")
        )
        self.assertEqual(saved["status"], "EDITED")
        self.assertTrue(saved["edited_at"])
        self.assertEqual(
            saved["original_analysis"][0]["analysis"]["product_identity"],
            "蓝色产品-1",
        )
        self.assertEqual(
            approved[0]["analysis"]["product_identity"], "人工确认的蓝色精华瓶"
        )
        self.assertEqual(approved[0]["analysis"]["colors"], ["深蓝", "白色"])
        self.assertIn("Logo位置", constraints["must_preserve"])
        self.assertIn("改变包装设计", constraints["avoid"])

    def test_C_cancel_records_rejected(self) -> None:
        with self.assertRaises(TaskCancelled):
            self.reviewer.review(
                self._analysis(),
                reanalyze=lambda: [],
                input_fn=lambda _prompt: "4",
                output=lambda _text: None,
            )
        saved = json.loads(
            self.paths.visual_analysis_review_path().read_text(encoding="utf-8")
        )
        self.assertEqual(saved["status"], "REJECTED")
        self.assertEqual(saved["history"][-1]["action"], "cancel")

    def test_D_reanalysis_rejects_old_result_then_approves_new_result(self) -> None:
        answers = iter(["3", "1"])
        approved, _constraints = self.reviewer.review(
            self._analysis(),
            reanalyze=lambda: self.layer.analyze_project_references(
                self.manager, self.request, force_refresh=True
            ),
            input_fn=lambda _prompt: next(answers),
            output=lambda _text: None,
        )
        self.assertEqual(self.provider.calls, 2)
        self.assertEqual(approved[0]["analysis"]["product_identity"], "蓝色产品-2")
        saved = json.loads(
            self.paths.visual_analysis_review_path().read_text(encoding="utf-8")
        )
        self.assertEqual(saved["status"], "APPROVED")
        self.assertEqual([item["status"] for item in saved["history"]], ["REJECTED", "APPROVED"])

    def test_E_prompt_ignores_historical_analysis_and_keeps_reference_presence(self) -> None:
        analysis, constraints = self.reviewer.review(
            self._analysis(),
            reanalyze=lambda: [],
            input_fn=lambda _prompt: "1",
            output=lambda _text: None,
        )
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
                visual_analysis_result=analysis,
                visual_constraints=constraints,
                reference_asset_context={"available": True, "asset_count": 1},
            )
        self.assertIn("不要人物", captured["user"])
        self.assertIn("已提供 1 张参考素材", captured["user"])
        self.assertNotIn("蓝色产品-1", captured["user"])
        self.assertNotIn("瓶身形状", captured["user"])

    def test_F_ab_switch_false_preserves_old_prompt_shape(self) -> None:
        config = self.base / "vision.json"
        config.write_text(
            json.dumps(
                {"visual_understanding_enabled": False, "providers": {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.assertFalse(visual_understanding_enabled(config))
        self.assertEqual(_visual_kwargs([], {"must_preserve": ["unused"]}), {})
        self.assertEqual(self.provider.calls, 0)

    def test_G_evaluation_records_are_secret_safe_and_project_scoped(self) -> None:
        analysis = self._analysis()
        self.evaluation.record_prompt(
            "creative",
            model="mock-deepseek",
            input_fields={
                "product": self.request.model_dump(),
                "api_key": "must-not-appear",
                "base64_image": "image-body",
            },
            output_result={"creative_concept": "test"},
        )
        checkpoint = ProjectCheckpoint.create(
            self.paths, self.request.product_name, self.request.model_dump()
        )
        checkpoint.ensure_shots([1])
        entry = checkpoint.shot_checkpoint(1)
        entry["generation_versions"] = [{"video_version": 1}]
        write_generation_snapshot(
            self.paths,
            1,
            1,
            {
                "provider": "mock-video",
                "provider_model": "mock-model",
                "provider_api_version": "v1",
                "generation_mode": "text_to_video",
                "prompt_version": 1,
                "status": "COMPLETED",
                "visual_input": {"mode": "none", "assets": []},
            },
        )
        checkpoint.data["assembly"].update(
            {
                "status": "COMPLETED",
                "final_video_version": 1,
                "final_video_path": "videos/final_video.mp4",
                "shot_versions": [{"shot_id": 1, "approved_video_version": 1}],
            }
        )
        self.evaluation.sync_generation_bundles(checkpoint)
        self.evaluation.sync_final(checkpoint)

        self.assertTrue(self.paths.evaluation_visual_analysis_path().is_file())
        self.assertTrue(self.paths.evaluation_prompt_path("creative").is_file())
        self.assertTrue(self.paths.evaluation_generation_path(1).is_file())
        self.assertTrue(self.paths.evaluation_final_video_path().is_file())
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                self.paths.evaluation_visual_analysis_path(),
                self.paths.evaluation_prompt_path("creative"),
                self.paths.evaluation_generation_path(1),
                self.paths.evaluation_final_video_path(),
            )
        )
        self.assertNotIn("must-not-appear", combined)
        self.assertNotIn("image-body", combined)
        self.assertIn("***REDACTED***", combined)
        self.assertEqual(analysis[0]["analysis"]["brand_style"], "克制高级")


if __name__ == "__main__":
    unittest.main()
