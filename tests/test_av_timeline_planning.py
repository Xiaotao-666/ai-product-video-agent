from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from prompt_generator import ProductVideoRequest
from review_manager import display_output
from shot_storage import new_shot_manifest
from storyboard import (
    CreativeBrief,
    NarrationPlan,
    Storyboard,
    StoryboardShot,
    SubtitleCue,
    SubtitleStrategy,
    VideoConstraints,
    VoiceoverCue,
    apply_video_overlay_constraints,
    build_global_av_timeline,
    generate_creative_brief,
    generate_storyboard,
    generate_video_prompts,
)


def response_with(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    return response


def request() -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="小蓝饮料",
        product_description="清爽果味饮料",
        user_notes="不出现人物",
        duration_seconds=12,
        video_style="年轻清爽",
        video_purpose="品牌宣传",
    )


def creative_payload(*, enabled: bool = True, narration_seconds: float = 8) -> dict:
    return {
        "creative_concept": "夏日清爽时刻",
        "target_audience": "年轻消费者",
        "key_message": "清爽有活力",
        "visual_direction": "明亮蓝色调",
        "narrative_arc": "产品登场到品牌收束",
        "narration_plan": {
            "enabled": enabled,
            "tone": "年轻、清爽" if enabled else "",
            "full_script": (
                "LEE柠檬鲜切为光，清新果香层层绽放，每一滴都唤醒明亮年轻活力。"
                if enabled
                else ""
            ),
            "target_duration_seconds": narration_seconds if enabled else 0,
        },
        "subtitle_strategy": {
            "enabled": enabled,
            "tone": "简洁、品牌化" if enabled else "",
            "density": "low",
            "max_lines": 1,
            "preferred_position": "bottom_center" if enabled else "none",
            "principles": ["短句优先"] if enabled else [],
        },
        "global_constraints": {"must": [], "must_not": ["people"]},
        "av_timeline_constraints": {"forbidden_windows": []},
    }


def brief() -> CreativeBrief:
    return CreativeBrief.model_validate(creative_payload())


def storyboard_payload(*, leaked_subtitle: str = "冰爽一刻") -> dict:
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "建立产品",
                "visual": "饮料瓶与冰块",
                "camera": "缓慢推进",
                "voiceover_cues": [
                    {
                        "text": "LEE柠檬鲜切为光，清新果香层层绽放，",
                        "start_offset": 0.4,
                        "end_offset": 4.2,
                    }
                ],
                "subtitle_cues": [
                    {
                        "text": leaked_subtitle,
                        "start_offset": 0.7,
                        "end_offset": 2.4,
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
                "visual": "产品定格",
                "camera": "固定镜头",
                "voiceover_cues": [
                    {
                        "text": "每一滴都唤醒明亮年轻活力。",
                        "start_offset": 0.2,
                        "end_offset": 4.4,
                    }
                ],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            },
        ],
    }


def storyboard_planning_payload(*, leaked_subtitle: str = "冰爽一刻") -> dict:
    payload = storyboard_payload(leaked_subtitle=leaked_subtitle)
    for shot in payload["shots"]:
        for cue in shot["voiceover_cues"]:
            cue.pop("start_offset")
            cue.pop("end_offset")
            cue["placement"] = "middle"
        for cue in shot["subtitle_cues"]:
            cue.pop("start_offset")
            cue.pop("end_offset")
            cue["placement"] = "middle"
    return payload


def board(*, leaked_subtitle: str = "冰爽一刻") -> Storyboard:
    return Storyboard.model_validate(
        storyboard_payload(leaked_subtitle=leaked_subtitle)
    )


class AVTimelinePlanningTests(unittest.TestCase):
    def test_01_creative_narration_plan_round_trip(self) -> None:
        value = brief()
        self.assertTrue(value.narration_plan.enabled)
        self.assertIn("清新果香", value.narration_plan.full_script)
        restored = CreativeBrief.model_validate(value.model_dump())
        self.assertEqual(restored.narration_plan, value.narration_plan)

    def test_02_creative_subtitle_strategy_round_trip(self) -> None:
        strategy = brief().subtitle_strategy
        self.assertEqual(strategy.density, "low")
        self.assertEqual(strategy.preferred_position, "bottom_center")
        self.assertEqual(strategy.max_lines, 1)

    def test_03_narration_disabled_is_valid(self) -> None:
        value = CreativeBrief.model_validate(creative_payload(enabled=False))
        self.assertFalse(value.narration_plan.enabled)
        self.assertEqual(value.narration_plan.target_duration_seconds, 0)

    def test_04_voiceover_cues_are_valid(self) -> None:
        cue = board().shots[0].voiceover_cues[0]
        self.assertEqual((cue.start_offset, cue.end_offset), (0.4, 4.2))

    def test_05_subtitle_cues_are_valid(self) -> None:
        cue = board().shots[0].subtitle_cues[0]
        self.assertEqual(cue.position, "bottom_center")

    def test_06_offset_beyond_shot_duration_is_rejected(self) -> None:
        payload = storyboard_payload()
        payload["shots"][0]["voiceover_cues"][0]["end_offset"] = 6.1
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(payload)

    def test_07_invalid_position_is_rejected(self) -> None:
        payload = storyboard_payload()
        payload["shots"][0]["subtitle_cues"][0]["position"] = "middle"
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(payload)

    def test_08_shot_without_subtitles_is_valid(self) -> None:
        shot = board().shots[1]
        self.assertEqual(shot.subtitle_cues, [])
        self.assertFalse(shot.video_constraints.reserve_subtitle_space)
        self.assertEqual(shot.video_constraints.subtitle_safe_area, "none")

    def test_09_safe_area_adds_composition_constraint(self) -> None:
        prompt = apply_video_overlay_constraints("产品特写", board().shots[0])
        self.assertIn("Reserve a clean, low-detail lower-center region", prompt)
        self.assertIn("important visual elements away", prompt)

    def test_10_no_subtitle_does_not_force_safe_area(self) -> None:
        prompt = apply_video_overlay_constraints("产品定格", board().shots[1])
        self.assertNotIn("Reserve a clean, low-detail", prompt)

    def test_11_video_prompt_never_receives_subtitle_body(self) -> None:
        secret_subtitle = "绝不发送的字幕正文XYZ"
        current_board = board(leaked_subtitle=secret_subtitle)
        response = response_with({"visual_prompt_core": "饮料瓶产品纯视觉特写"})
        with patch("prompt_generator.requests.post", return_value=response) as post:
            plan = generate_video_prompts(
                request(), brief(), current_board, "mock-deepseek-key"
            )
        request_text = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertNotIn(secret_subtitle, request_text)
        self.assertNotIn(secret_subtitle, plan.model_dump_json())

    def test_12_all_video_prompts_forbid_generated_overlays(self) -> None:
        for shot in board().shots:
            prompt = apply_video_overlay_constraints("纯画面", shot)
            self.assertIn("Do not generate subtitles, captions, title cards", prompt)
            self.assertIn("packaging text", prompt)
            self.assertIn("do not generate voice-over", prompt)

    def test_13_old_creative_uses_safe_defaults(self) -> None:
        old = {
            "creative_concept": "c",
            "target_audience": "a",
            "key_message": "k",
            "visual_direction": "v",
            "narrative_arc": "n",
        }
        restored = CreativeBrief.model_validate(old)
        self.assertFalse(restored.narration_plan.enabled)
        self.assertFalse(restored.subtitle_strategy.enabled)

    def test_14_old_storyboard_uses_safe_defaults(self) -> None:
        old = {
            "total_duration": 6,
            "shots": [
                {
                    "shot_id": 1,
                    "duration": 6,
                    "purpose": "p",
                    "visual": "v",
                    "camera": "c",
                }
            ],
        }
        restored = Storyboard.model_validate(old)
        shot = restored.shots[0]
        self.assertEqual(shot.voiceover_cues, [])
        self.assertEqual(shot.subtitle_cues, [])
        self.assertEqual(shot.video_constraints.subtitle_safe_area, "none")

    def test_15_shot_schema_v2_manifest_is_unchanged(self) -> None:
        manifest = new_shot_manifest(1)
        for planning_field in (
            "voiceover_cues",
            "subtitle_cues",
            "video_constraints",
        ):
            self.assertNotIn(planning_field, manifest)
        self.assertEqual(manifest["shot_schema_version"], 2)

    def test_16_only_subtitle_integration_reads_planned_subtitle_cues(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in (
            "voice_generation.py",
            "music_generation.py",
            "export_pipeline.py",
        ):
            source = (root / filename).read_text(encoding="utf-8")
            self.assertNotIn("voiceover_cues", source)
            self.assertNotIn("subtitle_cues", source)
        subtitle_source = (root / "subtitle_generation.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("build_global_av_timeline", subtitle_source)
        self.assertIn("subtitle_cues", subtitle_source)
        self.assertNotIn("voiceover_cues", subtitle_source)

    def test_17_global_timeline_conversion_is_pure_and_correct(self) -> None:
        current = board()
        before = current.model_dump()
        timeline = build_global_av_timeline(current)
        self.assertEqual(timeline["voiceover_cues"][0]["start"], 0.4)
        self.assertEqual(timeline["subtitle_cues"][0]["end"], 2.4)
        self.assertEqual(current.model_dump(), before)

    def test_18_storyboard_review_is_human_readable(self) -> None:
        stream = StringIO()
        with redirect_stdout(stream):
            display_output("Storyboard 分镜方案", board())
        rendered = stream.getvalue()
        self.assertIn("Voiceover：", rendered)
        self.assertIn("Subtitle：", rendered)
        self.assertIn("Subtitle Safe Area：bottom_center", rendered)

    def test_19_creative_review_shows_narration_and_subtitles(self) -> None:
        stream = StringIO()
        with redirect_stdout(stream):
            display_output("AI创意方案", brief())
        rendered = stream.getvalue()
        self.assertIn("旁白策略：启用", rendered)
        self.assertIn("字幕策略：启用", rendered)

    def test_20_invalid_storyboard_structure_retries(self) -> None:
        invalid = storyboard_planning_payload()
        invalid["shots"][0]["subtitle_cues"][0]["end_offset"] = 9
        valid = storyboard_planning_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            result = generate_storyboard(
                request(), brief(), "mock-deepseek-key"
            )
        self.assertEqual(post.call_count, 2)
        self.assertEqual([shot.shot_id for shot in result.shots], [1, 2])

    def test_21_narration_duration_retries(self) -> None:
        invalid = creative_payload(narration_seconds=13)
        valid = creative_payload(narration_seconds=8)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            result = generate_creative_brief(request(), "mock-deepseek-key")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.target_duration_seconds, 8)

    def test_22_non_numeric_offsets_are_rejected(self) -> None:
        payload = storyboard_payload()
        payload["shots"][0]["voiceover_cues"][0]["start_offset"] = "0.4"
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(payload)

    def test_23_overlapping_subtitles_are_rejected(self) -> None:
        payload = storyboard_payload()
        payload["shots"][0]["subtitle_cues"].append(
            {
                "text": "第二条",
                "start_offset": 2.0,
                "end_offset": 3.0,
                "position": "bottom_center",
            }
        )
        with self.assertRaises(ValidationError):
            Storyboard.model_validate(payload)

    def test_24_new_creative_output_cannot_omit_av_fields(self) -> None:
        old_shape = creative_payload()
        old_shape.pop("narration_plan")
        valid = creative_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(old_shape), response_with(valid)],
        ) as post:
            result = generate_creative_brief(request(), "mock-deepseek-key")
        self.assertEqual(post.call_count, 2)
        self.assertTrue(result.narration_plan.enabled)

    def test_25_new_storyboard_output_cannot_omit_av_fields(self) -> None:
        old_shape = storyboard_planning_payload()
        old_shape["shots"][0].pop("video_constraints")
        valid = storyboard_planning_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(old_shape), response_with(valid)],
        ) as post:
            result = generate_storyboard(request(), brief(), "mock-deepseek-key")
        self.assertEqual(post.call_count, 2)
        self.assertTrue(result.shots[0].video_constraints.reserve_subtitle_space)


if __name__ == "__main__":
    unittest.main()
