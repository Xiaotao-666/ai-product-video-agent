from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from prompt_generator import ProductVideoRequest
from storyboard import (
    AVForbiddenWindow,
    AVTimelineConstraints,
    CreativeBrief,
    Storyboard,
    StoryboardError,
    _validate_storyboard_av_timeline_constraints,
    _validate_storyboard_text_overlays,
    _video_planning_context,
    apply_video_overlay_constraints,
    extract_av_timeline_constraints,
    extract_global_constraints,
    generate_creative_brief,
    generate_video_prompts,
    merge_av_timeline_constraints,
    revise_creative_brief,
    revise_storyboard,
)


SCRIPT = "LEE柠檬鲜切为光，清新果香层层绽放，每一滴都唤醒明亮年轻活力。"


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
    notes: str = "", *, av: dict | None = None, narration: bool = True
) -> dict:
    if av is None:
        av = extract_av_timeline_constraints(notes, 12).model_dump()
    return {
        "creative_concept": "鲜切为光",
        "target_audience": "年轻消费者",
        "key_message": "鲜活清爽",
        "visual_direction": "明亮柠檬黄与水珠",
        "narrative_arc": "鲜果到产品收束",
        "narration_plan": {
            "enabled": narration,
            "tone": "清爽有活力" if narration else "",
            "full_script": SCRIPT if narration else "",
            "target_duration_seconds": 8 if narration else 0,
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
        "av_timeline_constraints": av,
    }


def brief(notes: str = "", *, narration: bool = True) -> CreativeBrief:
    return CreativeBrief.model_validate(
        creative_payload(notes, narration=narration)
    )


def storyboard_payload(
    *,
    first_voice: list[dict] | None = None,
    second_voice: list[dict] | None = None,
    first_subtitle: list[dict] | None = None,
    second_subtitle: list[dict] | None = None,
    purpose: str = "建立产品",
    visual: str = "柠檬与饮料产品特写",
    camera: str = "缓慢推进",
) -> dict:
    first_subtitle = first_subtitle or []
    second_subtitle = second_subtitle or []
    return {
        "total_duration": 12,
        "shots": [
            {
                "shot_id": 1,
                "duration": 6,
                "purpose": purpose,
                "visual": visual,
                "camera": camera,
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


def board(**kwargs) -> Storyboard:
    return Storyboard.model_validate(storyboard_payload(**kwargs))


class AVTimelinePhase12Tests(unittest.TestCase):
    def test_01_extract_opening_two_seconds_for_both_tracks(self) -> None:
        result = extract_av_timeline_constraints("前两秒不要旁白和字幕", 12)
        self.assertEqual(
            result.model_dump(),
            {"forbidden_windows": [{"start": 0.0, "end": 2.0, "tracks": ["voiceover", "subtitle"]}]},
        )

    def test_02_extract_pure_visual_window(self) -> None:
        result = extract_av_timeline_constraints("前三秒保持纯画面", 12)
        self.assertEqual(result.forbidden_windows[0].tracks, ["voiceover", "subtitle"])

    def test_03_extract_final_subtitle_window(self) -> None:
        result = extract_av_timeline_constraints("最后一秒不要字幕", 12)
        window = result.forbidden_windows[0]
        self.assertEqual((window.start, window.end, window.tracks), (11.0, 12.0, ["subtitle"]))

    def test_04_extract_decimal_voiceover_window(self) -> None:
        result = extract_av_timeline_constraints("开头1.5秒不要旁白", 12)
        window = result.forbidden_windows[0]
        self.assertEqual((window.start, window.end), (0.0, 1.5))

    def test_05_non_prohibitory_timing_note_is_ignored(self) -> None:
        result = extract_av_timeline_constraints("前三秒突出产品包装", 12)
        self.assertEqual(result.forbidden_windows, [])

    def test_06_merge_same_window_combines_tracks(self) -> None:
        merged = merge_av_timeline_constraints(
            extract_av_timeline_constraints("前两秒不要旁白", 12),
            extract_av_timeline_constraints("前两秒不要字幕", 12),
        )
        self.assertEqual(merged.forbidden_windows[0].tracks, ["voiceover", "subtitle"])

    def test_07_unknown_av_track_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AVForbiddenWindow(start=0, end=2, tracks=["music"])

    def test_08_invalid_forbidden_window_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AVForbiddenWindow(start=2, end=2, tracks=["subtitle"])

    def test_09_old_creative_defaults_to_empty_av_constraints(self) -> None:
        payload = creative_payload()
        payload.pop("av_timeline_constraints")
        self.assertEqual(
            CreativeBrief.model_validate(payload).av_timeline_constraints.forbidden_windows,
            [],
        )

    def test_10_creative_review_displays_av_constraints(self) -> None:
        text = brief("前两秒不要旁白和字幕").to_review_text()
        self.assertIn("AV 时间硬约束", text)
        self.assertIn("0s - 2s", text)

    def test_11_half_open_boundary_allows_cue_at_window_end(self) -> None:
        value = board(first_voice=[{"text": "开始", "start_offset": 2.0, "end_offset": 3.0}])
        constraints = extract_av_timeline_constraints("前两秒不要旁白", 12)
        _validate_storyboard_av_timeline_constraints(value, constraints)

    def test_12_voiceover_overlap_is_rejected(self) -> None:
        value = board(first_voice=[{"text": "过早", "start_offset": 1.9, "end_offset": 3.0}])
        with self.assertRaises(StoryboardError):
            _validate_storyboard_av_timeline_constraints(
                value, extract_av_timeline_constraints("前两秒不要旁白", 12)
            )

    def test_13_subtitle_overlap_is_rejected(self) -> None:
        value = board(first_subtitle=[{"text": "过早", "start_offset": 1.0, "end_offset": 2.5, "position": "bottom_center"}])
        with self.assertRaises(StoryboardError):
            _validate_storyboard_av_timeline_constraints(
                value, extract_av_timeline_constraints("前两秒不要字幕", 12)
            )

    def test_14_global_window_reaches_second_shot(self) -> None:
        value = board(second_subtitle=[{"text": "结尾", "start_offset": 5.2, "end_offset": 5.8, "position": "bottom_center"}])
        with self.assertRaises(StoryboardError):
            _validate_storyboard_av_timeline_constraints(
                value, extract_av_timeline_constraints("最后一秒不要字幕", 12)
            )

    def test_15_global_boundary_before_final_window_is_allowed(self) -> None:
        value = board(second_subtitle=[{"text": "收束", "start_offset": 4.0, "end_offset": 5.0, "position": "bottom_center"}])
        _validate_storyboard_av_timeline_constraints(
            value, extract_av_timeline_constraints("最后一秒不要字幕", 12)
        )

    def test_16_brand_name_fade_in_is_rejected(self) -> None:
        with self.assertRaises(StoryboardError):
            _validate_storyboard_text_overlays(board(visual="品牌名浮现于画面中央"))

    def test_17_screen_display_text_is_rejected(self) -> None:
        with self.assertRaises(StoryboardError):
            _validate_storyboard_text_overlays(board(visual="屏幕显示“LEE柠檬”"))

    def test_18_slogan_fade_in_is_rejected(self) -> None:
        with self.assertRaises(StoryboardError):
            _validate_storyboard_text_overlays(board(purpose="Slogan淡入并完成品牌收束"))

    def test_19_english_title_card_is_rejected(self) -> None:
        with self.assertRaises(StoryboardError):
            _validate_storyboard_text_overlays(board(camera="title card appears after push-in"))

    def test_20_real_product_name_in_scene_is_allowed(self) -> None:
        _validate_storyboard_text_overlays(board(visual="LEE柠檬产品位于画面中央"))

    def test_21_real_package_logo_is_allowed(self) -> None:
        _validate_storyboard_text_overlays(board(visual="包装 Logo 清晰可见，产品标签保持真实"))

    def test_22_generate_creative_persists_canonical_av_constraints(self) -> None:
        notes = "前两秒不要旁白和字幕"
        with patch("prompt_generator.requests.post", return_value=response_with(creative_payload(notes))) as post:
            result = generate_creative_brief(request(notes), "mock-key")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result.av_timeline_constraints.forbidden_windows[0].end, 2)

    def test_23_creative_review_feedback_adds_av_constraint(self) -> None:
        current = brief()
        revised_payload = creative_payload("", av={"forbidden_windows": [{"start": 11.0, "end": 12.0, "tracks": ["subtitle"]}]})
        with patch("prompt_generator.requests.post", return_value=response_with(revised_payload)):
            revised = revise_creative_brief(
                request(), current, "最后一秒不要字幕", "mock-key"
            )
        self.assertEqual(revised.av_timeline_constraints.forbidden_windows[0].start, 11)

    def test_24_storyboard_review_feedback_updates_and_persists_creative(self) -> None:
        current_brief = brief(narration=False)
        current_board = board()
        persist = Mock()
        with patch("prompt_generator.requests.post", return_value=response_with(storyboard_planning_payload())):
            revise_storyboard(
                request(), current_brief, current_board,
                "最后一秒不要字幕", "mock-key", persist_creative=persist,
            )
        self.assertEqual(current_brief.av_timeline_constraints.forbidden_windows[0].start, 11)
        persist.assert_called_once()

    def test_25_video_planning_context_excludes_subtitle_text(self) -> None:
        secret = "绝密字幕正文"
        value = board(first_subtitle=[{"text": secret, "start_offset": 2.0, "end_offset": 3.0, "position": "bottom_center"}])
        _, context = _video_planning_context(brief(), value)
        self.assertNotIn(secret, json.dumps(context, ensure_ascii=False))

    def test_26_video_planning_context_excludes_voiceover_text(self) -> None:
        secret = "绝密旁白正文"
        value = board(first_voice=[{"text": secret, "start_offset": 2.0, "end_offset": 3.0}])
        _, context = _video_planning_context(brief(), value)
        self.assertNotIn(secret, json.dumps(context, ensure_ascii=False))

    def test_27_program_appends_control_blocks_in_fixed_order(self) -> None:
        shot = board(first_subtitle=[{"text": "短句", "start_offset": 2.0, "end_offset": 3.0, "position": "bottom_center"}]).shots[0]
        prompt = apply_video_overlay_constraints("核心视觉", shot)
        headings = [
            "[Composition Constraint]", "[Global Hard Constraints]",
            "[Text Overlay Constraint]", "[Audio Constraint]",
        ]
        self.assertEqual([prompt.index(item) for item in headings], sorted(prompt.index(item) for item in headings))

    def test_28_text_rule_preserves_real_packaging_identity(self) -> None:
        prompt = apply_video_overlay_constraints("核心视觉", board().shots[0])
        self.assertIn("Do not generate subtitles", prompt)
        self.assertIn("Real logo, packaging text", prompt)

    def test_29_control_block_append_is_idempotent_and_legacy_safe(self) -> None:
        shot = board().shots[0]
        first = apply_video_overlay_constraints("核心视觉", shot)
        second = apply_video_overlay_constraints(first, shot)
        legacy = apply_video_overlay_constraints(
            "核心视觉\n\nPost-production overlay constraint:\nold rule", shot
        )
        self.assertEqual(first, second)
        self.assertNotIn("old rule", legacy)

    def test_30_video_prompt_request_never_contains_cue_bodies(self) -> None:
        subtitle_secret = "绝不能进入视频模型的字幕"
        voice_secret = "绝不能进入视频模型的旁白"
        current_board = board(
            first_voice=[{"text": voice_secret, "start_offset": 2.0, "end_offset": 3.0}],
            first_subtitle=[{"text": subtitle_secret, "start_offset": 2.0, "end_offset": 3.0, "position": "bottom_center"}],
        )
        valid = {"visual_prompt_core": "产品纯视觉特写"}
        with patch("prompt_generator.requests.post", return_value=response_with(valid)) as post:
            generate_video_prompts(request(), brief(), current_board, "mock-key")
        sent = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertNotIn(subtitle_secret, sent)
        self.assertNotIn(voice_secret, sent)

    def test_31_retry_feedback_never_leaks_subtitle_body(self) -> None:
        secret = "绝密字幕不可回传"
        current_board = board(first_subtitle=[{"text": secret, "start_offset": 2.0, "end_offset": 3.0, "position": "bottom_center"}])
        invalid = {"visual_prompt_core": f"画面显示{secret}"}
        valid = {"visual_prompt_core": "产品纯视觉特写"}
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with(invalid), response_with(valid), response_with(valid)],
        ) as post:
            generate_video_prompts(request(), brief(), current_board, "mock-key")
        retry_content = post.call_args_list[1].kwargs["json"]["messages"][1]["content"]
        self.assertNotIn(secret, retry_content)
        self.assertIn("后期文字或旁白内容", retry_content)

    def test_32_only_subtitle_integration_reads_av_constraints(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for filename in (
            "voice_generation.py", "music_generation.py", "export_pipeline.py",
        ):
            self.assertNotIn(
                "av_timeline_constraints",
                (root / filename).read_text(encoding="utf-8"),
            )
        self.assertIn(
            "av_timeline_constraints",
            (root / "subtitle_generation.py").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
