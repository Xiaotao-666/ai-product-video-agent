from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from prompt_generator import ProductVideoRequest, PromptGenerationError
from storyboard import (
    CreativeBrief,
    GlobalConstraints,
    ShotVideoPrompt,
    Storyboard,
    StoryboardShot,
    SubtitleCue,
    VideoConstraints,
    VideoPromptPlan,
    VideoPromptStructureError,
    _extract_visual_prompt_core,
    _validate_video_prompt_payload,
    _validate_visual_prompt_core,
    apply_video_overlay_constraints,
    generate_video_prompts,
)


def response_with(payload: dict | str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def request() -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="LEE柠檬",
        product_description="清新柠檬产品",
        user_notes="不要出现人物，镜头平稳，与参考素材风格保持一致",
        duration_seconds=18,
        video_style="简洁高级",
        video_purpose="品牌宣传",
    )


def brief() -> CreativeBrief:
    return CreativeBrief(
        creative_concept="清新产品视觉",
        target_audience="大众",
        key_message="清新",
        visual_direction="稳定镜头、明亮自然光",
        narrative_arc="产品逐步揭示",
        global_constraints=GlobalConstraints(must=[], must_not=["people"]),
    )


def board(*, secret_subtitle: str | None = None) -> Storyboard:
    shots: list[StoryboardShot] = []
    for shot_id in (1, 2, 3):
        cues = []
        constraints = VideoConstraints(
            reserve_subtitle_space=False, subtitle_safe_area="none"
        )
        if shot_id == 1 and secret_subtitle:
            cues = [
                SubtitleCue(
                    text=secret_subtitle,
                    start_offset=1.0,
                    end_offset=2.0,
                    position="bottom_center",
                )
            ]
            constraints = VideoConstraints(
                reserve_subtitle_space=True,
                subtitle_safe_area="bottom_center",
            )
        shots.append(
            StoryboardShot(
                shot_id=shot_id,
                duration=6,
                purpose=f"purpose-{shot_id}",
                visual=f"visual-only-{shot_id}",
                camera=f"camera-{shot_id}",
                subtitle_cues=cues,
                video_constraints=constraints,
            )
        )
    return Storyboard(total_duration=18, shots=shots)


def core(value: str) -> Mock:
    return response_with({"visual_prompt_core": value})


class VideoPromptStabilityTests(unittest.TestCase):
    def test_01_to_05_per_shot_generation_and_program_owned_ids(self) -> None:
        current_board = board()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[core("core-1"), core("core-2"), core("core-3")],
        ) as post:
            plan = generate_video_prompts(
                request(),
                brief(),
                current_board,
                "mock-key",
                reference_asset_context={"asset_count": 1},
            )
        self.assertEqual(post.call_count, 3)  # 1
        for index, call in enumerate(post.call_args_list, start=1):
            user_text = call.kwargs["json"]["messages"][1]["content"]
            self.assertIn(f"visual-only-{index}", user_text)  # 2
            self.assertNotIn('"shot_id"', user_text)  # 3
            for other in {1, 2, 3} - {index}:
                self.assertNotIn(f"visual-only-{other}", user_text)
        self.assertEqual([item.shot_id for item in plan.shots], [1, 2, 3])  # 4
        self.assertEqual(
            [item.visual_prompt_core for item in plan.shots],
            ["core-1", "core-2", "core-3"],
        )  # 5: no bulk shot-count/missing-ID structure remains

    def test_06_to_09_partial_success_is_persisted_and_resumed(self) -> None:
        current_board = board()
        invalid = core("a young woman holds the lemon")
        with tempfile.TemporaryDirectory() as temp:
            paths = create_project_paths(Path(temp) / "project")
            progress = paths.video_prompt_generation_progress_path()
            with patch(
                "prompt_generator.requests.post",
                side_effect=[core("shot-one-core"), invalid, invalid, invalid],
            ) as first_post:
                with self.assertRaises(PromptGenerationError):
                    generate_video_prompts(
                        request(), brief(), current_board, "mock-key", progress_path=progress
                    )
            self.assertEqual(first_post.call_count, 4)  # 6: only Shot 02 retried
            state = json.loads(progress.read_text(encoding="utf-8"))
            self.assertEqual(state["shots"][0]["status"], "COMPLETED")  # 7
            self.assertEqual(state["shots"][1]["status"], "FAILED")  # 8
            self.assertEqual(state["shots"][2]["status"], "NOT_STARTED")

            with patch(
                "prompt_generator.requests.post",
                side_effect=[core("shot-two-core"), core("shot-three-core")],
            ) as resumed_post:
                plan = generate_video_prompts(
                    request(), brief(), current_board, "mock-key", progress_path=progress
                )
            self.assertEqual(resumed_post.call_count, 2)  # 9: Shot 01 was skipped
            self.assertEqual(plan.shots[0].visual_prompt_core, "shot-one-core")

    def test_10_people_positive_request_fails(self) -> None:
        with self.assertRaises(VideoPromptStructureError):
            _validate_visual_prompt_core(
                "a young woman holds the lemon", board().shots[0], brief().global_constraints
            )

    def test_11_and_12_negative_people_language_is_not_a_violation(self) -> None:
        shot = board().shots[0]
        final_prompt = apply_video_overlay_constraints(
            "clean product shot", shot, brief().global_constraints
        )
        _validate_video_prompt_payload(
            {
                "shots": [
                    {
                        "shot_id": 1,
                        "visual_prompt_core": "clean product shot",
                        "video_prompt": final_prompt,
                    }
                ]
            },
            Storyboard(total_duration=6, shots=[shot]),
            brief().global_constraints,
        )  # 11: programmatic "No people" is not scanned as content
        _validate_visual_prompt_core(
            "A clean product composition without any people.",
            shot,
            brief().global_constraints,
        )  # 12

    def test_13_final_prompt_contains_hard_constraint_block(self) -> None:
        prompt = apply_video_overlay_constraints(
            "product macro", board().shots[0], brief().global_constraints
        )
        self.assertIn("[Global Hard Constraints]", prompt)
        self.assertIn("No people, human figures", prompt)

    def test_14_to_17_overlay_rules_allow_product_identity(self) -> None:
        shot = board().shots[0]
        with self.assertRaises(VideoPromptStructureError):
            _validate_visual_prompt_core(
                "品牌名作为标题浮现", shot, brief().global_constraints, "LEE柠檬"
            )  # 14
        _validate_visual_prompt_core(
            "一颗完整的LEE柠檬位于画面中央",
            shot,
            brief().global_constraints,
            "LEE柠檬",
        )  # 15
        _validate_visual_prompt_core(
            "包装上的Logo保持原样",
            shot,
            brief().global_constraints,
            "LEE柠檬",
        )  # 16
        prompt = apply_video_overlay_constraints("产品特写", shot)
        self.assertIn("[Text Overlay Constraint]", prompt)  # 17

    def test_18_to_21_content_and_retry_isolation(self) -> None:
        subtitle_secret = "新鲜绝密字幕"
        voice_secret = "绝密旁白正文"
        current_board = board(secret_subtitle=subtitle_secret)
        # Add a voice cue body without changing the visual planning input.
        payload = current_board.model_dump()
        payload["shots"][0]["voiceover_cues"] = [
            {"text": voice_secret, "start_offset": 2.0, "end_offset": 3.0}
        ]
        current_board = Storyboard.model_validate(payload)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                core(f"画面显示{subtitle_secret}"),
                core("产品特写"),
                core("产品侧面"),
                core("产品定格"),
            ],
        ) as post:
            generate_video_prompts(request(), brief(), current_board, "mock-key")
        first_request = post.call_args_list[0].kwargs["json"]["messages"][1]["content"]
        retry_request = post.call_args_list[1].kwargs["json"]["messages"][1]["content"]
        self.assertNotIn(subtitle_secret, first_request)  # 18
        self.assertNotIn(voice_secret, first_request)  # 19
        self.assertNotIn(subtitle_secret, retry_request)  # 20
        self.assertNotIn(voice_secret, retry_request)  # 21

    def test_22_to_26_control_blocks_are_deterministic(self) -> None:
        subtitle_board = board(secret_subtitle="短字幕")
        with_subtitle = apply_video_overlay_constraints(
            "产品特写", subtitle_board.shots[0], brief().global_constraints
        )
        self.assertIn("Reserve a clean, low-detail lower-center", with_subtitle)  # 22
        self.assertIn("[Global Hard Constraints]", with_subtitle)  # 23
        self.assertIn("[Text Overlay Constraint]", with_subtitle)  # 24
        self.assertIn("[Audio Constraint]", with_subtitle)  # 25
        no_subtitle = apply_video_overlay_constraints(
            "产品定格", board().shots[1], brief().global_constraints
        )
        self.assertNotIn("Reserve a clean, low-detail", no_subtitle)  # 26

    def test_27_legacy_video_prompt_plan_remains_loadable(self) -> None:
        restored = VideoPromptPlan.model_validate(
            {"shots": [{"shot_id": 1, "video_prompt": "legacy prompt"}]}
        )
        self.assertIsNone(restored.shots[0].visual_prompt_core)
        self.assertEqual(restored.shots[0].video_prompt, "legacy prompt")

    def test_28_to_30_progress_is_outside_shot_schema_and_planning_data_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = create_project_paths(Path(temp) / "project")
            progress = paths.video_prompt_generation_progress_path()
            self.assertEqual(progress.parent, paths.storyboard_dir)  # 28
            self.assertFalse(str(progress).startswith(str(paths.shots_dir)))  # 28
        original_board = board().model_dump()
        original_brief = brief().model_dump()
        with patch(
            "prompt_generator.requests.post",
            side_effect=[core("one"), core("two"), core("three")],
        ):
            generate_video_prompts(request(), brief(), board(), "mock-key")
        self.assertEqual(board().model_dump(), original_board)  # 29
        self.assertEqual(brief().model_dump(), original_brief)  # 30

    def test_31_to_33_generation_does_not_touch_external_pipelines(self) -> None:
        with (
            patch("prompt_generator.requests.post", side_effect=[core("one"), core("two"), core("three")]),
            patch("video_generator.generate_video") as minimax,
            patch("voice_generation.generate_confirmed_voice") as tts,
            patch("subprocess.run") as ffmpeg,
        ):
            plan = generate_video_prompts(request(), brief(), board(), "mock-key")
        self.assertEqual(len(plan.shots), 3)
        minimax.assert_not_called()  # 31
        tts.assert_not_called()  # 32
        ffmpeg.assert_not_called()  # 33

    def test_final_core_can_be_recovered_without_control_blocks(self) -> None:
        prompt = apply_video_overlay_constraints(
            "stable core", board().shots[0], brief().global_constraints
        )
        self.assertEqual(_extract_visual_prompt_core(prompt), "stable core")


if __name__ == "__main__":
    unittest.main()
