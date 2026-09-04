from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from project_manager import create_project_paths
from prompt_generator import (
    DEEPSEEK_API_URL,
    MAX_JSON_REQUEST_ATTEMPTS,
    ProductVideoRequest,
    StructuredOutputError,
    StructuredOutputExhaustedError,
    deepseek_json_request,
)
from storyboard import (
    CreativeBrief,
    _creative_duration_retry_feedback,
    generate_creative_brief,
)
from task_logger import TaskLogger
from timeline_scheduler import estimate_narration_duration


SHORT_SCRIPT = "OIP-C耳机，声音更清晰，佩戴更轻盈。"
MATCHING_SCRIPT = (
    "戴上OIP-C耳机，让轻盈设计贴合每一次出发。"
    "清晰音质自然展开，从通勤到休憩，舒适陪伴始终在线。"
)
LONG_SCRIPT = (
    "OIP-C耳机以轻盈设计贴合双耳，清晰音质从第一拍开始层层展开。"
    "无论清晨通勤、午后专注，还是夜晚放松，它都让细节自然抵达，"
    "让舒适陪伴每一次聆听。"
)
CASE_8_11 = "轻" * 27 + "OIP-C"
CASE_20_64 = "声" * 71 + "。"
CASE_4_89 = "净" * 14 + "，，，。"


def request() -> ProductVideoRequest:
    return ProductVideoRequest(
        product_name="OIP-C耳机",
        product_description="轻盈佩戴与清晰音质",
        duration_seconds=30,
        video_style="clean modern",
        video_purpose="品牌宣传",
    )


def creative_payload(
    *,
    script: str = MATCHING_SCRIPT,
    target: float = 12,
    enabled: bool = True,
) -> dict:
    return {
        "creative_concept": "轻盈入耳，清晰随行",
        "target_audience": "重视日常聆听体验的年轻用户",
        "key_message": "轻盈佩戴与清晰音质兼得",
        "visual_direction": "极简产品特写与柔和冷光",
        "narrative_arc": "从佩戴进入沉浸聆听并以产品收束",
        "narration_plan": {
            "enabled": enabled,
            "tone": "克制、清晰、现代" if enabled else "",
            "full_script": script if enabled else "",
            "target_duration_seconds": target if enabled else 0,
        },
        "subtitle_strategy": {
            "enabled": True,
            "tone": "简洁现代",
            "density": "low",
            "max_lines": 1,
            "preferred_position": "bottom_center",
            "principles": ["短句优先"],
        },
        "global_constraints": {"must": [], "must_not": []},
        "av_timeline_constraints": {"forbidden_windows": []},
    }


def response_with_payload(payload: dict) -> Mock:
    return response_with_text(json.dumps(payload, ensure_ascii=False))


def response_with_text(content: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def retry_prompt(post: Mock, call_index: int = 1) -> str:
    return post.call_args_list[call_index].kwargs["json"]["messages"][1]["content"]


class CreativeNarrationDurationRetryHotfixTests(unittest.TestCase):
    def _generate_with(self, *payloads: dict) -> tuple[CreativeBrief, Mock]:
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with_payload(payload) for payload in payloads],
        ) as post:
            result = generate_creative_brief(request(), "mock-key")
        return result, post

    def test_01_json_syntax_failure_is_classified_as_json_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "json-error"
            )
            with patch(
                "prompt_generator.requests.post",
                side_effect=[response_with_text("{broken"), response_with_text('{"ok":1}')],
            ) as post:
                result = deepseek_json_request(
                    "mock-key", "system", "user", task_logger=logger
                )
            self.assertEqual(result, {"ok": 1})
            self.assertIn("无法解析为合法 JSON", retry_prompt(post))
            api_log = logger.api_log_path.read_text(encoding="utf-8")
            self.assertIn("LLM_JSON_PARSE_ERROR", api_log)
            self.assertNotIn("LLM_SCHEMA_VALIDATION_ERROR", api_log)

    def test_02_schema_failure_is_classified_separately(self) -> None:
        def require_ok(data: dict) -> None:
            if "ok" not in data:
                raise StructuredOutputError("缺少 ok")

        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "schema-error"
            )
            with patch(
                "prompt_generator.requests.post",
                side_effect=[response_with_text('{"wrong":1}'), response_with_text('{"ok":1}')],
            ) as post:
                result = deepseek_json_request(
                    "mock-key",
                    "system",
                    "user",
                    task_logger=logger,
                    structure_validator=require_ok,
                )
            self.assertEqual(result, {"ok": 1})
            self.assertIn("通过 Schema 校验", retry_prompt(post))
            api_log = logger.api_log_path.read_text(encoding="utf-8")
            self.assertIn("LLM_SCHEMA_VALIDATION_ERROR", api_log)
            self.assertNotIn("LLM_JSON_PARSE_ERROR", api_log)

    def test_03_short_script_feedback_says_expand(self) -> None:
        invalid = creative_payload(script=SHORT_SCRIPT)
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "business-error"
            )
            with patch(
                "prompt_generator.requests.post",
                side_effect=[
                    response_with_payload(invalid),
                    response_with_payload(creative_payload()),
                ],
            ) as post:
                generate_creative_brief(request(), "mock-key", logger)
            api_log = logger.api_log_path.read_text(encoding="utf-8")
        feedback = retry_prompt(post)
        self.assertIn("Creative 业务校验", feedback)
        self.assertIn("required_direction=EXPAND（扩写 full_script）", feedback)
        self.assertIn("LLM_CREATIVE_BUSINESS_VALIDATION_ERROR", api_log)
        self.assertNotIn("LLM_JSON_PARSE_ERROR", api_log)

    def test_04_short_script_feedback_forbids_target_only_change(self) -> None:
        _, post = self._generate_with(
            creative_payload(script=SHORT_SCRIPT), creative_payload()
        )
        feedback = retry_prompt(post)
        self.assertIn("不要只修改 target_duration_seconds", feedback)
        self.assertIn("扩写 full_script", feedback)

    def test_05_long_script_feedback_says_compress(self) -> None:
        _, post = self._generate_with(
            creative_payload(script=LONG_SCRIPT), creative_payload()
        )
        self.assertIn(
            "required_direction=COMPRESS（压缩 full_script）",
            retry_prompt(post),
        )

    def test_06_feedback_contains_target_script_estimate_delta_and_direction(self) -> None:
        brief = CreativeBrief.model_validate(creative_payload(script=SHORT_SCRIPT))
        feedback = _creative_duration_retry_feedback(brief)
        estimated = estimate_narration_duration(SHORT_SCRIPT)
        self.assertIn("current target_duration_seconds=12", feedback)
        self.assertIn(f"current full_script={SHORT_SCRIPT}", feedback)
        self.assertIn(f"current estimated_duration={estimated:g} 秒", feedback)
        self.assertIn("delta_seconds(estimated-target)=", feedback)
        self.assertIn("required_direction=EXPAND", feedback)

    def test_07_feedback_requires_other_creative_fields_to_stay_stable(self) -> None:
        brief = CreativeBrief.model_validate(creative_payload(script=SHORT_SCRIPT))
        feedback = _creative_duration_retry_feedback(brief)
        for field in (
            "creative_concept",
            "key_message",
            "narration_plan.tone",
            "subtitle_strategy",
            "global_constraints",
            "av_timeline_constraints",
        ):
            self.assertIn(field, feedback)
        self.assertIn("其他 Creative 字段不变", feedback)

    def test_08_second_longer_script_passes_without_changing_creative(self) -> None:
        invalid = creative_payload(script=SHORT_SCRIPT)
        valid = creative_payload(script=MATCHING_SCRIPT)
        result, post = self._generate_with(invalid, valid)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.full_script, MATCHING_SCRIPT)
        self.assertEqual(result.narration_plan.target_duration_seconds, 12)
        self.assertEqual(result.creative_concept, invalid["creative_concept"])
        self.assertEqual(result.key_message, invalid["key_message"])
        self.assertEqual(result.narration_plan.tone, invalid["narration_plan"]["tone"])

    def test_09_lowering_valid_target_is_rejected_and_retry_continues(self) -> None:
        lowered = creative_payload(script=SHORT_SCRIPT, target=5)
        result, post = self._generate_with(
            creative_payload(script=SHORT_SCRIPT, target=12),
            lowered,
            creative_payload(script=MATCHING_SCRIPT, target=12),
        )
        self.assertEqual(post.call_count, 3)
        self.assertEqual(result.narration_plan.target_duration_seconds, 12)
        third_prompt = retry_prompt(post, 2)
        self.assertIn("current target_duration_seconds=5", third_prompt)
        self.assertIn("planned target_duration_seconds=12", third_prompt)
        self.assertIn("不能把目标改成 5 来迁就当前文案", third_prompt)

    def test_10_three_duration_failures_stop_at_limit_and_use_fallback(self) -> None:
        invalid = creative_payload(script=SHORT_SCRIPT)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with_payload(copy.deepcopy(invalid))
                for _ in range(MAX_JSON_REQUEST_ATTEMPTS)
            ],
        ) as post:
            result = generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, MAX_JSON_REQUEST_ATTEMPTS)
        self.assertEqual(result.narration_plan.full_script, SHORT_SCRIPT)

    def test_11_retry_call_limit_remains_three(self) -> None:
        self.assertEqual(MAX_JSON_REQUEST_ATTEMPTS, 3)

    def test_12_disabled_narration_is_unaffected(self) -> None:
        result, post = self._generate_with(creative_payload(enabled=False))
        self.assertFalse(result.narration_plan.enabled)
        self.assertEqual(result.narration_plan.target_duration_seconds, 0)
        self.assertEqual(post.call_count, 1)

    def test_13_matching_narration_is_unaffected(self) -> None:
        result, post = self._generate_with(creative_payload())
        self.assertEqual(result.narration_plan.full_script, MATCHING_SCRIPT)
        self.assertEqual(post.call_count, 1)

    def test_14_only_mocked_deepseek_endpoint_is_touched(self) -> None:
        with patch(
            "prompt_generator.requests.post",
            return_value=response_with_payload(creative_payload()),
        ) as post:
            generate_creative_brief(request(), "mock-key")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], DEEPSEEK_API_URL)

    def test_15_out_of_bounds_target_may_be_corrected(self) -> None:
        result, post = self._generate_with(
            creative_payload(script=LONG_SCRIPT, target=31),
            creative_payload(script=MATCHING_SCRIPT, target=12),
        )
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.target_duration_seconds, 12)
        feedback = retry_prompt(post)
        self.assertIn("旁白预计时长不得超过视频总时长", feedback)
        self.assertNotIn("planned target_duration_seconds=31", feedback)


class CreativeNarrationBestEffortFallbackTests(unittest.TestCase):
    def _run(
        self,
        responses: list[dict | str],
        *,
        product_request: ProductVideoRequest | None = None,
        task_logger: TaskLogger | None = None,
    ) -> tuple[CreativeBrief, Mock]:
        provider_responses = [
            response_with_text(value)
            if isinstance(value, str)
            else response_with_payload(value)
            for value in responses
        ]
        with patch(
            "prompt_generator.requests.post",
            side_effect=provider_responses,
        ) as post:
            result = generate_creative_brief(
                product_request or request(),
                "mock-key",
                task_logger,
            )
        return result, post

    @staticmethod
    def _log_text(logger: TaskLogger) -> str:
        paths = (logger.task_log_path, logger.api_log_path, logger.error_log_path)
        return "\n".join(
            path.read_text(encoding="utf-8") if path.exists() else ""
            for path in paths
        )

    def test_fallback_01_first_strict_pass_does_not_use_fallback(self) -> None:
        result, post = self._run([creative_payload()])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result.narration_plan.full_script, MATCHING_SCRIPT)

    def test_fallback_02_second_strict_pass_wins_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "strict-second"
            )
            result, post = self._run(
                [
                    creative_payload(script=CASE_8_11),
                    creative_payload(script=MATCHING_SCRIPT),
                ],
                task_logger=logger,
            )
            logs = self._log_text(logger)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.narration_plan.full_script, MATCHING_SCRIPT)
        self.assertNotIn("BEST_EFFORT_FALLBACK_USED", logs)

    def test_fallback_03_three_duration_mismatches_choose_smallest_gap(self) -> None:
        result, post = self._run(
            [
                creative_payload(script=CASE_4_89),
                creative_payload(script=CASE_8_11),
                creative_payload(script=CASE_20_64),
            ]
        )
        self.assertEqual(post.call_count, 3)
        self.assertEqual(result.narration_plan.full_script, CASE_8_11)

    def test_fallback_04_real_case_selects_attempt_one_at_8_11(self) -> None:
        self.assertEqual(estimate_narration_duration(CASE_8_11), 8.11)
        self.assertEqual(estimate_narration_duration(CASE_20_64), 20.64)
        self.assertEqual(estimate_narration_duration(CASE_4_89), 4.89)
        result, _ = self._run(
            [
                creative_payload(script=CASE_8_11),
                creative_payload(script=CASE_20_64),
                creative_payload(script=CASE_4_89),
            ]
        )
        self.assertEqual(result.narration_plan.full_script, CASE_8_11)

    def test_fallback_05_duration_gap_is_absolute_difference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "gap"
            )
            self._run(
                [
                    creative_payload(script=CASE_8_11),
                    creative_payload(script=CASE_20_64),
                    creative_payload(script=CASE_4_89),
                ],
                task_logger=logger,
            )
            logs = self._log_text(logger)
        self.assertIn("duration_gap_seconds=3.89", logs)

    def test_fallback_06_tie_prefers_earlier_attempt(self) -> None:
        first = "轻" * 27 + "OIP-C"
        second = "声" * 27 + "OIP-C"
        self.assertEqual(
            estimate_narration_duration(first), estimate_narration_duration(second)
        )
        result, _ = self._run(
            [
                creative_payload(script=first),
                creative_payload(script=second),
                creative_payload(script=CASE_4_89),
            ]
        )
        self.assertEqual(result.narration_plan.full_script, first)

    def test_fallback_07_candidate_over_video_duration_is_excluded(self) -> None:
        short_video_request = request().model_copy(update={"duration_seconds": 12})
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with_payload(creative_payload(script=CASE_8_11)),
                response_with_payload(creative_payload(script=CASE_20_64)),
                response_with_payload(creative_payload(script=CASE_4_89)),
            ],
        ) as post:
            with self.assertRaises(StructuredOutputExhaustedError):
                generate_creative_brief(short_video_request, "mock-key")
        self.assertEqual(post.call_count, 3)

    def test_fallback_08_json_invalid_attempt_never_enters_pool(self) -> None:
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with_text("{broken"),
                response_with_payload(creative_payload(script=CASE_8_11)),
                response_with_payload(creative_payload(script=CASE_4_89)),
            ],
        ) as post:
            with self.assertRaises(StructuredOutputExhaustedError):
                generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, 3)

    def test_fallback_09_schema_invalid_attempt_never_enters_pool(self) -> None:
        invalid_schema = creative_payload(script=CASE_8_11)
        invalid_schema.pop("key_message")
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with_payload(invalid_schema),
                response_with_payload(creative_payload(script=CASE_8_11)),
                response_with_payload(creative_payload(script=CASE_4_89)),
            ],
        ) as post:
            with self.assertRaises(StructuredOutputExhaustedError):
                generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, 3)

    def test_fallback_10_other_business_error_never_enters_pool(self) -> None:
        wrong_constraints = creative_payload(script=CASE_8_11)
        wrong_constraints["global_constraints"] = {"must": ["不存在的约束"], "must_not": []}
        with patch(
            "prompt_generator.requests.post",
            side_effect=[
                response_with_payload(creative_payload(script=CASE_8_11)),
                response_with_payload(wrong_constraints),
                response_with_payload(creative_payload(script=CASE_4_89)),
            ],
        ) as post:
            with self.assertRaises(StructuredOutputExhaustedError):
                generate_creative_brief(request(), "mock-key")
        self.assertEqual(post.call_count, 3)

    def test_fallback_11_no_eligible_candidate_still_fails(self) -> None:
        short_video_request = request().model_copy(update={"duration_seconds": 12})
        invalid = creative_payload(script=CASE_20_64)
        with patch(
            "prompt_generator.requests.post",
            side_effect=[response_with_payload(copy.deepcopy(invalid)) for _ in range(3)],
        ) as post:
            with self.assertRaises(StructuredOutputExhaustedError):
                generate_creative_brief(short_video_request, "mock-key")
        self.assertEqual(post.call_count, 3)

    def test_fallback_12_best_effort_adds_no_provider_calls(self) -> None:
        _, post = self._run(
            [
                creative_payload(script=CASE_8_11),
                creative_payload(script=CASE_20_64),
                creative_payload(script=CASE_4_89),
            ]
        )
        self.assertEqual(post.call_count, 3)

    def test_fallback_13_retry_limit_remains_three(self) -> None:
        self.assertEqual(MAX_JSON_REQUEST_ATTEMPTS, 3)

    def test_fallback_14_logs_best_effort_and_selected_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "best-effort"
            )
            self._run(
                [
                    creative_payload(script=CASE_8_11),
                    creative_payload(script=CASE_20_64),
                    creative_payload(script=CASE_4_89),
                ],
                task_logger=logger,
            )
            logs = self._log_text(logger)
        self.assertIn("strict validation: FAILED after 3 attempts", logs)
        self.assertIn("Best-effort fallback: USED", logs)
        self.assertIn("selected_attempt=1", logs)
        self.assertIn("target_duration_seconds=12.0", logs)
        self.assertIn("estimated_duration_seconds=8.11", logs)
        self.assertIn("duration_gap_seconds=3.89", logs)

    def test_fallback_15_business_fallback_is_not_described_as_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            logger = TaskLogger(
                create_project_paths(Path(temp) / "project"), "not-json"
            )
            self._run(
                [
                    creative_payload(script=CASE_8_11),
                    creative_payload(script=CASE_20_64),
                    creative_payload(script=CASE_4_89),
                ],
                task_logger=logger,
            )
            logs = self._log_text(logger)
        self.assertNotIn("连续 3 次返回无效结构化 JSON", logs)
        self.assertNotIn("LLM_JSON_PARSE_ERROR", logs)

    def test_fallback_16_narration_disabled_is_unchanged(self) -> None:
        result, post = self._run([creative_payload(enabled=False)])
        self.assertFalse(result.narration_plan.enabled)
        self.assertEqual(post.call_count, 1)

    def test_fallback_17_normal_creative_is_unchanged(self) -> None:
        result, post = self._run([creative_payload()])
        self.assertEqual(result.model_dump(), CreativeBrief.model_validate(creative_payload()).model_dump())
        self.assertEqual(post.call_count, 1)

    def test_fallback_18_no_downstream_stage_or_media_tool_is_called(self) -> None:
        with (
            patch("storyboard.generate_storyboard") as storyboard_stage,
            patch("voice_generation.generate_confirmed_voice") as voice_stage,
            patch("subtitle_generation.generate_subtitle_for_project") as subtitle_stage,
            patch("video_generator.generate_video") as shot_stage,
            patch("subprocess.run") as process,
            patch(
                "prompt_generator.requests.post",
                side_effect=[
                    response_with_payload(creative_payload(script=CASE_8_11)),
                    response_with_payload(creative_payload(script=CASE_20_64)),
                    response_with_payload(creative_payload(script=CASE_4_89)),
                ],
            ),
        ):
            generate_creative_brief(request(), "mock-key")
        storyboard_stage.assert_not_called()
        voice_stage.assert_not_called()
        subtitle_stage.assert_not_called()
        shot_stage.assert_not_called()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
