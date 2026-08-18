from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from prompt_generator import ProductVideoRequest
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardShot,
    apply_video_overlay_constraints,
    estimate_narration_duration,
    extract_global_constraints,
    generate_creative_brief,
    generate_storyboard,
    generate_video_prompts,
    narration_duration_is_consistent,
    normalize_camera_language,
    revise_creative_brief,
    _validate_storyboard,
)


LONG_SCRIPT = "LEE柠檬鲜切为光，清新果香层层绽放，每一滴都唤醒明亮年轻活力。"
FIRST_SCRIPT = "LEE柠檬鲜切为光，清新果香层层绽放，"
SECOND_SCRIPT = "每一滴都唤醒明亮年轻活力。"


def response_with(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    return response


def request(notes: str = "") -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="LEE柠檬",
        product_description="鲜榨柠檬饮料",
        user_notes=notes,
        duration_seconds=12,
        video_style="清爽年轻",
        video_purpose="品牌宣传",
    )


def creative_payload(
    *,
    script: str = LONG_SCRIPT,
    target: float = 8,
    enabled: bool = True,
    must_not: list[str] | None = None,
) -> dict:
    return {
        "creative_concept": "鲜切为光",
        "target_audience": "年轻消费者",
        "key_message": "鲜活清爽",
        "visual_direction": "明亮柠檬黄与水珠",
        "narrative_arc": "鲜果到产品收束",
        "narration_plan": {
            "enabled": enabled,
            "tone": "清爽有活力" if enabled else "",
            "full_script": script if enabled else "",
            "target_duration_seconds": target if enabled else 0,
        },
        "subtitle_strategy": {
            "enabled": True,
            "tone": "简洁品牌化",
            "density": "low",
            "max_lines": 1,
            "preferred_position": "bottom_center",
            "principles": ["短句优先"],
        },
        "global_constraints": {"must": [], "must_not": must_not or []},
        "av_timeline_constraints": {"forbidden_windows": []},
    }


def brief(*, must_not: list[str] | None = None) -> CreativeBrief:
    return CreativeBrief.model_validate(
        creative_payload(must_not=must_not)
    )


def storyboard_payload(
    *,
    first_visual: str = "柠檬与饮料产品特写",
    first_text: str = FIRST_SCRIPT,
    second_text: str = SECOND_SCRIPT,
    first_end: float = 4.2,
    second_end: float = 4.4,
) -> dict:
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "建立产品",
                "visual": first_visual,
                "camera": "缓慢推进",
                "voiceover_cues": [
                    {"text": first_text, "start_offset": 0.4, "end_offset": first_end}
                ],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            },
            {
                "shot_id": 2,
                "duration": 6,
                "purpose": "品牌收束",
                "visual": "产品与水珠定格",
                "camera": "固定镜头",
                "voiceover_cues": [
                    {"text": second_text, "start_offset": 0.2, "end_offset": second_end}
                ],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            },
        ],
    }


def storyboard_planning_payload(**kwargs) -> dict:
    payload = storyboard_payload(**kwargs)
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


class AVTimelinePhase11Tests(unittest.TestCase):
    def test_01_matching_narration_duration_passes(self) -> None:
        estimated = estimate_narration_duration(LONG_SCRIPT)
        self.assertGreater(estimated, 6)
        self.assertTrue(narration_duration_is_consistent(LONG_SCRIPT, 8))

    def test_02_short_script_declaring_eight_seconds_retries(self) -> None:
        invalid = creative_payload(script="LEE柠檬，鲜活。", target=8)
        valid = creative_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            result = generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.full_script, LONG_SCRIPT)

    def test_03_duration_revision_must_rewrite_script(self) -> None:
        current = CreativeBrief.model_validate(
            creative_payload(script="LEE柠檬，鲜活。", target=4)
        )
        unchanged = creative_payload(script="LEE柠檬，鲜活。", target=8)
        valid = creative_payload(script=LONG_SCRIPT, target=8)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(unchanged), response_with(valid)],
        ) as post:
            revised = revise_creative_brief(
                request(), current, "旁白8-10秒", "mock-key"
            )
        self.assertEqual(post.call_count, 2)
        self.assertNotEqual(
            revised.narration_plan.full_script,
            current.narration_plan.full_script,
        )
        self.assertGreaterEqual(revised.narration_plan.target_duration_seconds, 8)

    def test_04_narration_disabled_remains_valid(self) -> None:
        payload = creative_payload(enabled=False)
        with patch(
            "prompt_generator.requests.post", return_value=response_with(payload)
        ) as post:
            result = generate_creative_brief(request(), "mock-key")
        self.assertFalse(result.narration_plan.enabled)
        self.assertEqual(post.call_count, 1)

    def test_05_target_cannot_exceed_total_duration(self) -> None:
        invalid = creative_payload(script=LONG_SCRIPT * 2, target=13)
        valid = creative_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            result = generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.target_duration_seconds, 8)

    def test_06_timeline_span_is_not_used_as_spoken_duration(self) -> None:
        value = Storyboard.model_validate(
            storyboard_payload(first_end=2.0, second_end=1.2)
        )
        _validate_storyboard(value, request(), brief())

    def test_07_storyboard_must_cover_creative_narration(self) -> None:
        invalid = storyboard_planning_payload(
            first_text="鲜活。",
            second_text="清爽。",
        )
        valid = storyboard_planning_payload()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            board = generate_storyboard(request(), brief(), "mock-key")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(board.shots[0].voiceover_cues[0].text, FIRST_SCRIPT)

    def test_08_no_people_is_extracted_as_hard_constraint(self) -> None:
        constraints = extract_global_constraints("不要出现人物，清爽，年轻")
        self.assertEqual(constraints.must_not, ["people"])

    def test_09_creative_preferences_are_not_must_not_constraints(self) -> None:
        constraints = extract_global_constraints("清爽、年轻、高级")
        self.assertEqual(constraints.must, [])
        self.assertEqual(constraints.must_not, [])

    def test_10_storyboard_people_violation_retries(self) -> None:
        invalid = storyboard_planning_payload(first_visual="年轻女性拿起柠檬产品")
        valid = storyboard_planning_payload()
        no_people_brief = brief(must_not=["people"])
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            board = generate_storyboard(
                request("不要出现人物，清爽，年轻"),
                no_people_brief,
                "mock-key",
            )
        self.assertEqual(post.call_count, 2)
        self.assertNotIn("女性", board.shots[0].visual)

    def test_11_video_prompt_explicitly_includes_no_people(self) -> None:
        shot = Storyboard.model_validate(storyboard_payload()).shots[0]
        prompt = apply_video_overlay_constraints(
            "柠檬产品特写",
            shot,
            extract_global_constraints("不要出现人物"),
        )
        self.assertIn("[Global Hard Constraints]", prompt)
        self.assertIn("No people, human figures, hands, faces", prompt)

    def test_12_old_creative_defaults_to_empty_constraints(self) -> None:
        payload = creative_payload()
        payload.pop("global_constraints")
        restored = CreativeBrief.model_validate(payload)
        self.assertEqual(restored.global_constraints.must, [])
        self.assertEqual(restored.global_constraints.must_not, [])

    def test_13_camera_conflicts_and_high_fps_are_normalized(self) -> None:
        normalized = normalize_camera_language(
            "轻微俯角90度，每秒1000帧拍摄柠檬水花。"
        )
        self.assertNotIn("轻微俯角90度", normalized)
        self.assertNotIn("1000", normalized)
        self.assertIn("90-degree overhead shot", normalized)
        self.assertIn("extreme slow-motion commercial motion", normalized)

    def test_14_hard_constraints_do_not_enter_postproduction_modules(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for filename in (
            "voice_generation.py",
            "subtitle_generation.py",
            "music_generation.py",
            "export_pipeline.py",
        ):
            source = (root / filename).read_text(encoding="utf-8")
            self.assertNotIn("global_constraints", source)

    def test_15_video_prompt_people_violation_retries(self) -> None:
        current_board = Storyboard.model_validate(storyboard_payload())
        no_people_brief = brief(must_not=["people"])
        invalid = {"visual_prompt_core": "年轻女性手持柠檬饮料"}
        valid_1 = {"visual_prompt_core": "柠檬饮料与水珠产品特写"}
        valid_2 = {"visual_prompt_core": "产品定格"}
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with(invalid),
                response_with(valid_1),
                response_with(valid_2),
            ],
        ) as post:
            plan = generate_video_prompts(
                request("不要出现人物"),
                no_people_brief,
                current_board,
                "mock-key",
            )
        self.assertEqual(post.call_count, 3)
        self.assertIn("No people, human figures", plan.shots[0].video_prompt)


if __name__ == "__main__":
    unittest.main()
