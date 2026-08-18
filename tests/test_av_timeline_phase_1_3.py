from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from prompt_generator import ProductVideoRequest
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardError,
    StoryboardPlanning,
    _validate_storyboard_narration,
    _video_planning_context,
    build_global_av_timeline,
    compile_storyboard_planning,
    estimate_narration_duration,
    estimate_subtitle_duration,
    generate_storyboard,
    revise_storyboard,
    storyboard_to_planning,
)
from timeline_scheduler import TimelineScheduleError, schedule_av_timeline


SCRIPT = "LEE柠檬鲜切为光，清新果香层层绽放，每一滴都唤醒明亮年轻活力。"
FIRST = "LEE柠檬鲜切为光，清新果香绽放。"
SECOND = "每一滴都唤醒明亮年轻活力。"


def response_with(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]
    }
    return response


def request(notes: str = "", duration: int = 12) -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="LEE柠檬",
        product_description="鲜榨柠檬饮料",
        user_notes=notes,
        duration_seconds=duration,
        video_style="清爽年轻",
        video_purpose="品牌宣传",
    )


def brief(
    *,
    narration: bool = True,
    target: float = 8,
    notes: str = "",
) -> CreativeBrief:
    from storyboard import extract_av_timeline_constraints, extract_global_constraints

    return CreativeBrief.model_validate(
        {
            "creative_concept": "鲜切为光",
            "target_audience": "年轻消费者",
            "key_message": "鲜活清爽",
            "visual_direction": "明亮柠檬黄与水珠",
            "narrative_arc": "鲜果到产品收束",
            "narration_plan": {
                "enabled": narration,
                "tone": "清爽有活力" if narration else "",
                "full_script": SCRIPT if narration else "",
                "target_duration_seconds": target if narration else 0,
            },
            "subtitle_strategy": {
                "enabled": True,
                "tone": "简洁品牌化",
                "density": "low",
                "max_lines": 1,
                "preferred_position": "bottom_center",
                "principles": ["短句优先"],
            },
            "global_constraints": extract_global_constraints(notes).model_dump(),
            "av_timeline_constraints": extract_av_timeline_constraints(
                notes, 12
            ).model_dump(),
        }
    )


def planning_payload(
    *,
    first_voice: list[dict] | None = None,
    second_voice: list[dict] | None = None,
    first_subtitle: list[dict] | None = None,
    second_subtitle: list[dict] | None = None,
) -> dict:
    first_subtitle = first_subtitle or []
    second_subtitle = second_subtitle or []
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "建立产品",
                "visual": "柠檬与饮料产品特写",
                "camera": "缓慢推进",
                "voiceover_cues": first_voice or [],
                "subtitle_cues": first_subtitle,
                "video_constraints": {
                    "reserve_subtitle_space": bool(first_subtitle),
                    "subtitle_safe_area": "bottom_center" if first_subtitle else "none",
                },
            },
            {
                "shot_id": 2,
                "duration": 6,
                "purpose": "品牌收束",
                "visual": "产品与水珠定格",
                "camera": "固定镜头",
                "voiceover_cues": second_voice or [],
                "subtitle_cues": second_subtitle,
                "video_constraints": {
                    "reserve_subtitle_space": bool(second_subtitle),
                    "subtitle_safe_area": "bottom_center" if second_subtitle else "none",
                },
            },
        ],
    }


def compiled_payload() -> dict:
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": "建立产品",
                "visual": "产品特写",
                "camera": "缓慢推进",
                "voiceover_cues": [
                    {"text": FIRST, "start_offset": 0.2, "end_offset": 1.0}
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
                "visual": "产品定格",
                "camera": "固定镜头",
                "voiceover_cues": [
                    {"text": SECOND, "start_offset": 4.8, "end_offset": 5.7}
                ],
                "subtitle_cues": [],
                "video_constraints": {
                    "reserve_subtitle_space": False,
                    "subtitle_safe_area": "none",
                },
            },
        ],
    }


class AVTimelinePhase13Tests(unittest.TestCase):
    def test_01_planning_accepts_supported_placements(self) -> None:
        for placement in ("auto", "start", "middle", "end"):
            payload = planning_payload(
                first_subtitle=[
                    {"text": "新鲜", "placement": placement, "position": "bottom_center"}
                ]
            )
            StoryboardPlanning.model_validate(payload)

    def test_02_planning_rejects_unknown_placement(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "新鲜", "placement": "later"}]
        )
        with self.assertRaises(ValidationError):
            StoryboardPlanning.model_validate(payload)

    def test_03_planning_rejects_exact_timestamp_fields(self) -> None:
        payload = planning_payload(
            first_voice=[
                {"text": "新鲜", "placement": "middle", "start_offset": 1.0}
            ]
        )
        with self.assertRaises(ValidationError):
            StoryboardPlanning.model_validate(payload)

    def test_04_blank_subtitle_is_structure_invalid(self) -> None:
        payload = planning_payload(
            first_subtitle=[
                {"text": "   ", "placement": "middle", "position": "bottom_center"}
            ]
        )
        with self.assertRaises(ValidationError):
            StoryboardPlanning.model_validate(payload)

    def test_05_short_subtitle_has_minimum_reading_time(self) -> None:
        self.assertGreaterEqual(estimate_subtitle_duration("新鲜"), 1.2)

    def test_06_long_subtitle_is_capped(self) -> None:
        self.assertLessEqual(estimate_subtitle_duration("很长的品牌字幕内容" * 20), 4.5)

    def test_07_scheduler_is_deterministic(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新活力", "placement": "auto"}]
        )
        first = schedule_av_timeline(payload, {"forbidden_windows": []})
        second = schedule_av_timeline(payload, {"forbidden_windows": []})
        self.assertEqual(first, second)

    def test_08_start_placement_respects_edge_padding(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新", "placement": "start"}]
        )
        cue = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["voiceover_cues"][0]
        self.assertEqual(cue["start_offset"], 0.2)

    def test_09_middle_placement_is_centered(self) -> None:
        payload = planning_payload(
            first_subtitle=[
                {"text": "清新活力", "placement": "middle", "position": "bottom_center"}
            ]
        )
        cue = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["subtitle_cues"][0]
        self.assertAlmostEqual((cue["start_offset"] + cue["end_offset"]) / 2, 3.0, places=3)

    def test_10_end_placement_respects_edge_padding(self) -> None:
        payload = planning_payload(
            first_subtitle=[
                {"text": "清新", "placement": "end", "position": "bottom_center"}
            ]
        )
        cue = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["subtitle_cues"][0]
        self.assertEqual(cue["end_offset"], 5.8)

    def test_11_voice_forbidden_window_does_not_block_subtitle(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新", "placement": "start"}],
            first_subtitle=[
                {"text": "清新", "placement": "start", "position": "bottom_center"}
            ],
        )
        result = schedule_av_timeline(
            payload,
            {"forbidden_windows": [{"start": 0.0, "end": 2.0, "tracks": ["voiceover"]}]},
        )["shots"][0]
        self.assertEqual(result["voiceover_cues"][0]["start_offset"], 2.0)
        self.assertEqual(result["subtitle_cues"][0]["start_offset"], 0.2)

    def test_12_both_tracks_obey_forbidden_window(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新", "placement": "start"}],
            first_subtitle=[
                {"text": "清新", "placement": "start", "position": "bottom_center"}
            ],
        )
        result = schedule_av_timeline(
            payload,
            {"forbidden_windows": [{"start": 0.0, "end": 2.0, "tracks": ["voiceover", "subtitle"]}]},
        )["shots"][0]
        self.assertEqual(result["voiceover_cues"][0]["start_offset"], 2.0)
        self.assertEqual(result["subtitle_cues"][0]["start_offset"], 2.0)

    def test_13_cue_may_start_at_forbidden_window_end(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新", "placement": "start"}]
        )
        cue = schedule_av_timeline(
            payload,
            {"forbidden_windows": [{"start": 0.0, "end": 2.0, "tracks": ["voiceover"]}]},
        )["shots"][0]["voiceover_cues"][0]
        self.assertEqual(cue["start_offset"], 2.0)

    def test_14_cue_never_crosses_shot_boundary(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": "清新活力", "placement": "end"}]
        )
        cue = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["voiceover_cues"][0]
        self.assertLessEqual(cue["end_offset"], 5.8)

    def test_15_unsatisfiable_voice_content_has_explicit_code(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": SCRIPT * 3, "placement": "middle"}]
        )
        with self.assertRaises(TimelineScheduleError) as raised:
            schedule_av_timeline(payload, {"forbidden_windows": []})
        self.assertIn("SCHEDULE_UNSATISFIABLE", str(raised.exception))
        self.assertIn("Shot 1 voiceover", str(raised.exception))

    def test_16_multiple_voice_cues_do_not_overlap(self) -> None:
        payload = planning_payload(
            first_voice=[
                {"text": "清新", "placement": "start"},
                {"text": "活力", "placement": "start"},
            ]
        )
        cues = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["voiceover_cues"]
        self.assertGreaterEqual(cues[1]["start_offset"] - cues[0]["end_offset"], 0.149)

    def test_17_multiple_subtitles_do_not_overlap(self) -> None:
        payload = planning_payload(
            first_subtitle=[
                {"text": "清新", "placement": "start", "position": "bottom_center"},
                {"text": "活力", "placement": "middle", "position": "bottom_center"},
                {"text": "年轻", "placement": "end", "position": "bottom_center"},
            ]
        )
        cues = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["subtitle_cues"]
        self.assertTrue(all(left["end_offset"] <= right["start_offset"] for left, right in zip(cues, cues[1:])))

    def test_18_scheduler_allows_silence_gap(self) -> None:
        payload = planning_payload(
            first_voice=[
                {"text": "清新", "placement": "start"},
                {"text": "活力", "placement": "end"},
            ]
        )
        cues = schedule_av_timeline(payload, {"forbidden_windows": []})["shots"][0]["voiceover_cues"]
        self.assertGreater(cues[1]["start_offset"], cues[0]["end_offset"])

    def test_19_compiler_returns_existing_storyboard_schema(self) -> None:
        planning = StoryboardPlanning.model_validate(
            planning_payload(
                first_voice=[{"text": FIRST, "placement": "middle"}],
                second_voice=[{"text": SECOND, "placement": "middle"}],
            )
        )
        result = compile_storyboard_planning(planning, request(), brief())
        self.assertIsInstance(result, Storyboard)
        self.assertIn("start_offset", result.model_dump()["shots"][0]["voiceover_cues"][0])

    def test_20_narration_consistency_uses_text_not_timeline_span(self) -> None:
        value = Storyboard.model_validate(compiled_payload())
        _validate_storyboard_narration(value, brief())

    def test_21_narration_text_duration_mismatch_is_rejected(self) -> None:
        value = Storyboard.model_validate(compiled_payload())
        with self.assertRaises(StoryboardError):
            _validate_storyboard_narration(value, brief(target=4))

    def test_22_generate_storyboard_compiles_semantic_output(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": FIRST, "placement": "middle"}],
            second_voice=[{"text": SECOND, "placement": "middle"}],
        )
        with patch("storyboard.deepseek_json_request", return_value=payload):
            result = generate_storyboard(request(), brief(), "mock-key")
        self.assertIsInstance(result, Storyboard)
        self.assertGreater(result.shots[0].voiceover_cues[0].end_offset, 0)

    def test_23_storyboard_prompt_forbids_llm_timestamps(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": FIRST, "placement": "middle"}],
            second_voice=[{"text": SECOND, "placement": "middle"}],
        )
        captured: dict[str, str] = {}

        def fake(_key, system, user, **_kwargs):
            captured["system"] = system
            captured["user"] = user
            return payload

        with patch("storyboard.deepseek_json_request", side_effect=fake):
            generate_storyboard(request(), brief(), "mock-key")
        self.assertIn("严禁输出 start_offset、end_offset", captured["system"])
        self.assertIn('"placement":"middle"', captured["system"])

    def test_24_unsatisfiable_retry_is_semantic_and_contains_no_cue_text(self) -> None:
        secret = "只存在于失败输出的绝密超长旁白" * 20
        invalid = planning_payload(
            first_voice=[{"text": secret, "placement": "middle"}]
        )
        valid = planning_payload(
            first_voice=[{"text": FIRST, "placement": "middle"}],
            second_voice=[{"text": SECOND, "placement": "middle"}],
        )
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid)],
        ) as post:
            generate_storyboard(request(), brief(), "mock-key")
        retry = post.call_args_list[1].kwargs["json"]["messages"][1]["content"]
        self.assertNotIn(secret, retry)
        self.assertIn("SCHEDULE_UNSATISFIABLE", retry)
        self.assertIn("不得输出 start/end 时间", retry)

    def test_25_review_revision_receives_placements_not_offsets(self) -> None:
        current = Storyboard.model_validate(compiled_payload())
        payload = storyboard_to_planning(current).model_dump()
        captured: dict[str, str] = {}

        def fake(_key, _system, user, **_kwargs):
            captured["user"] = user
            return payload

        with patch("storyboard.deepseek_json_request", side_effect=fake):
            revise_storyboard(request(), brief(), current, "旁白晚一点", "mock-key")
        self.assertIn('"placement"', captured["user"])
        self.assertNotIn('"start_offset"', captured["user"])
        self.assertNotIn('"end_offset"', captured["user"])

    def test_26_review_end_placement_recompiles_later(self) -> None:
        current = Storyboard.model_validate(compiled_payload())
        payload = storyboard_to_planning(current).model_dump()
        payload["shots"][0]["voiceover_cues"][0]["placement"] = "end"
        with patch("storyboard.deepseek_json_request", return_value=payload):
            result = revise_storyboard(
                request(), brief(), current, "第一句旁白晚一点出现", "mock-key"
            )
        self.assertGreater(result.shots[0].voiceover_cues[0].start_offset, 1.0)

    def test_27_old_compiled_storyboard_still_loads_without_rescheduling(self) -> None:
        payload = compiled_payload()
        restored = Storyboard.model_validate(payload)
        self.assertEqual(restored.model_dump(), payload)

    def test_28_video_prompt_context_has_no_cues_or_times(self) -> None:
        current = Storyboard.model_validate(compiled_payload())
        _, context = _video_planning_context(brief(), current)
        serialized = json.dumps(context, ensure_ascii=False)
        for forbidden in ("voiceover_cues", "subtitle_cues", "start_offset", "end_offset"):
            self.assertNotIn(forbidden, serialized)

    def test_29_global_timeline_uses_compiled_offsets(self) -> None:
        current = Storyboard.model_validate(compiled_payload())
        timeline = build_global_av_timeline(current)
        self.assertEqual(timeline["voiceover_cues"][1]["start"], 10.8)

    def test_30_scheduler_has_no_network_dependency(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "timeline_scheduler.py").read_text(encoding="utf-8")
        for dependency in ("requests", "DeepSeek", "MINIMAX", "Gemini"):
            self.assertNotIn(dependency, source)

    def test_31_postproduction_does_not_import_scheduler(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in (
            "voice_generation.py", "subtitle_generation.py",
            "music_generation.py", "export_pipeline.py",
        ):
            self.assertNotIn(
                "timeline_scheduler",
                (root / filename).read_text(encoding="utf-8"),
            )

    def test_32_padding_can_yield_to_otherwise_legal_content(self) -> None:
        payload = planning_payload(
            first_voice=[{"text": FIRST, "placement": "start"}]
        )
        cue = schedule_av_timeline(
            payload,
            {"forbidden_windows": [{"start": 0.0, "end": 2.0, "tracks": ["voiceover"]}]},
        )["shots"][0]["voiceover_cues"][0]
        self.assertEqual(cue["start_offset"], 2.0)
        self.assertLessEqual(cue["end_offset"], 6.0)


if __name__ == "__main__":
    unittest.main()
