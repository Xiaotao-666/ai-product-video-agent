"""Shared DeepSeek JSON client and prompt safety review."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Mapping

import requests
from pydantic import BaseModel, Field, ValidationError

from task_logger import TaskLogger


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
REQUEST_TIMEOUT_SECONDS = 120
MAX_JSON_REQUEST_ATTEMPTS = 3
logger = logging.getLogger(__name__)


class ProductVideoRequest(BaseModel):
    product_name: str = Field(min_length=1)
    product_description: str = Field(min_length=1)
    user_notes: str = ""
    duration_seconds: int = Field(gt=0)
    video_style: str = Field(min_length=1)
    video_purpose: str = Field(min_length=1)


class PromptSafetyReview(BaseModel):
    is_safe: bool
    risk_notes: list[str]
    reviewed_video_prompt: str = Field(min_length=1)


class PromptGenerationError(RuntimeError):
    """Raised when DeepSeek cannot return a usable structured result."""


class StructuredOutputExhaustedError(PromptGenerationError):
    """Raised after every structured-output validation attempt is rejected."""


def extract_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or {}
        return str(error.get("message") or payload.get("message") or "")
    except (ValueError, AttributeError):
        return ""


class DeepSeekJSONFormatError(ValueError):
    """Raised only when DeepSeek content cannot be parsed as JSON locally."""


class StructuredOutputError(ValueError):
    """Raised when syntactically valid JSON violates the required schema."""

    def __init__(
        self,
        message: str,
        *,
        expected_ids: list[Any] | None = None,
        actual_ids: list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_ids = expected_ids
        self.actual_ids = actual_ids


class CreativeBusinessValidationError(ValueError):
    """Raised when a schema-valid Creative result violates business rules."""

    def __init__(
        self,
        message: str,
        *,
        retry_feedback: str = "",
        duration_candidate: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_feedback = retry_feedback
        self.duration_candidate = dict(duration_candidate or {})


class JSONDuplicateKeyError(DeepSeekJSONFormatError):
    """Raised when any JSON object contains the same key more than once."""

    def __init__(self, duplicate_key: str) -> None:
        self.duplicate_key = duplicate_key
        super().__init__(f"JSON 对象包含重复键：{duplicate_key}")


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JSONDuplicateKeyError(key)
        result[key] = value
    return result


def strict_json_loads(content: str) -> Any:
    """Load JSON while rejecting duplicate keys at every object depth."""
    return json.loads(content, object_pairs_hook=_strict_object_pairs)


def strip_markdown_fence(content: str) -> str:
    cleaned = content.lstrip("\ufeff").strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL
    )
    return fenced.group(1).strip() if fenced else cleaned


def escape_illegal_control_characters(content: str) -> str:
    """Escape raw control characters inside JSON strings and drop them outside."""
    output: list[str] = []
    inside_string = False
    escaped = False
    for character in content:
        codepoint = ord(character)
        if inside_string:
            if escaped:
                output.append(character)
                escaped = False
                continue
            if character == "\\":
                output.append(character)
                escaped = True
                continue
            if character == '"':
                output.append(character)
                inside_string = False
                continue
            if character == "\n":
                output.append("\\n")
            elif character == "\r":
                output.append("\\r")
            elif character == "\t":
                output.append("\\t")
            elif codepoint < 0x20:
                output.append(f"\\u{codepoint:04x}")
            else:
                output.append(character)
            continue

        if character == '"':
            inside_string = True
            output.append(character)
        elif codepoint >= 0x20 or character in "\n\r\t":
            output.append(character)
    return "".join(output)


def extract_json_object(content: str) -> str:
    """Keep the first balanced top-level object when surrounding text exists."""
    start = content.find("{")
    if start < 0:
        return content
    depth = 0
    inside_string = False
    escaped = False
    for index in range(start, len(content)):
        character = content[index]
        if inside_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                inside_string = False
            continue
        if character == '"':
            inside_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
    return content[start:]


def parse_deepseek_json(content: str) -> dict[str, Any]:
    """Parse JSON using a conservative cleanup followed by one repair pass."""
    cleaned = escape_illegal_control_characters(strip_markdown_fence(content))
    try:
        parsed = strict_json_loads(cleaned)
    except JSONDuplicateKeyError:
        raise
    except json.JSONDecodeError as first_error:
        repaired = extract_json_object(cleaned)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            parsed = strict_json_loads(repaired)
        except JSONDuplicateKeyError:
            raise
        except json.JSONDecodeError as second_error:
            raise DeepSeekJSONFormatError(
                f"初次解析：{first_error}; 自动修复：{second_error}"
            ) from second_error
    if not isinstance(parsed, dict):
        raise DeepSeekJSONFormatError("DeepSeek 顶层 JSON 必须是对象。")
    return parsed


JSON_ONLY_SUFFIX = """

强制输出要求：只返回一个合法 JSON 对象。禁止 Markdown 代码块、解释文字、注释和 JSON 前后缀。禁止在任何 JSON 对象中重复使用相同的 key。JSON 字符串内如需换行，必须使用转义字符 \\n，不得输出原始换行或其他未转义控制字符。
""".rstrip()


def deepseek_json_request(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    model: str = DEEPSEEK_MODEL,
    max_tokens: int = 4000,
    task_logger: TaskLogger | None = None,
    raw_stage: str = "llm",
    structure_validator: Callable[[dict[str, Any]], None] | None = None,
    retry_instruction: str | None = None,
    log_fields: Mapping[str, Any] | None = None,
    retry_preamble: str | None = None,
    creative_best_effort_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not api_key.strip():
        raise PromptGenerationError("DEEPSEEK_API_KEY 不能为空。")
    json_system_prompt = system_prompt + JSON_ONLY_SUFFIX
    last_validation_error: Exception | None = None
    last_validation_category = "Unknown Validation Error"
    retry_feedback = ""
    context_fields = dict(log_fields or {})
    duration_candidates: list[dict[str, Any]] = []
    for attempt in range(1, MAX_JSON_REQUEST_ATTEMPTS + 1):
        raw_content: str | None = None
        raw_path = None
        try:
            if task_logger:
                task_logger.api(
                    "LLM_REQUESTED",
                    "DeepSeek",
                    stage=raw_stage,
                    attempt=attempt,
                    model=model,
                    **context_fields,
                )
            response = requests.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": json_system_prompt},
                        {
                            "role": "user",
                            "content": user_prompt + retry_feedback,
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "max_tokens": max_tokens,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not content or not str(content).strip():
                raise DeepSeekJSONFormatError("DeepSeek 返回了空内容。")
            raw_content = str(content)
            if task_logger:
                raw_path = task_logger.save_llm_raw(raw_stage, raw_content)
            parsed = parse_deepseek_json(raw_content)
            if structure_validator is not None:
                structure_validator(parsed)
            if task_logger:
                task_logger.api(
                    "LLM_COMPLETED",
                    "DeepSeek",
                    stage=raw_stage,
                    attempt=attempt,
                    **context_fields,
                )
            return parsed
        except (
            DeepSeekJSONFormatError,
            StructuredOutputError,
            CreativeBusinessValidationError,
        ) as exc:
            last_validation_error = exc
            if isinstance(exc, CreativeBusinessValidationError):
                last_validation_category = "Creative Business Validation Error"
                log_event = "LLM_CREATIVE_BUSINESS_VALIDATION_ERROR"
                warning_label = "Creative 业务校验异常"
                retry_message = (
                    "你刚才的 JSON 已成功解析并通过 Schema 校验，但未通过 "
                    "Creative 业务校验。"
                )
                if creative_best_effort_callback is not None and exc.duration_candidate:
                    duration_candidates.append(
                        {
                            "data": parsed,
                            "attempt": attempt,
                            **exc.duration_candidate,
                        }
                    )
            elif isinstance(exc, StructuredOutputError):
                last_validation_category = "Schema Validation Error"
                log_event = "LLM_SCHEMA_VALIDATION_ERROR"
                warning_label = "Schema 校验异常"
                retry_message = (
                    "你刚才的 JSON 已成功解析，但未通过 Schema 校验，请重新生成完整结果。"
                )
            else:
                last_validation_category = "JSON Parse Error"
                log_event = "LLM_JSON_PARSE_ERROR"
                warning_label = "JSON 解析异常"
                retry_message = "你刚才的内容无法解析为合法 JSON，请重新生成完整结果。"
            if task_logger:
                task_logger.error(exc, stage=raw_stage)
                task_logger.api(
                    log_event,
                    "DeepSeek",
                    stage=raw_stage,
                    attempt=attempt,
                    **context_fields,
                )
                if isinstance(exc, JSONDuplicateKeyError):
                    task_logger.event(
                        "STRUCTURED_JSON_DUPLICATE_KEY",
                        duplicate_key=exc.duplicate_key,
                        retry_index=attempt,
                        raw_response_path=raw_path,
                        **context_fields,
                    )
                if raw_stage.startswith("video_prompt"):
                    task_logger.event(
                        "VIDEO_PROMPT_STRUCTURE_INVALID",
                        expected_shot_ids=getattr(exc, "expected_ids", None),
                        actual_shot_ids=getattr(exc, "actual_ids", None),
                        retry_index=attempt,
                        raw_response_path=raw_path,
                        reason=exc,
                        **context_fields,
                    )
            logger.warning(
                "DeepSeek %s（第 %s/%s 次）：%s",
                warning_label,
                attempt,
                MAX_JSON_REQUEST_ATTEMPTS,
                exc,
            )
            if attempt < MAX_JSON_REQUEST_ATTEMPTS:
                print(f"AI返回{warning_label}，正在重新请求结构化输出")
                if task_logger and raw_stage.startswith("video_prompt"):
                    task_logger.event(
                        "VIDEO_PROMPT_RETRY",
                        retry_index=attempt + 1,
                        max_attempts=MAX_JSON_REQUEST_ATTEMPTS,
                        reason=exc,
                        **context_fields,
                    )
                retry_feedback = f"\n\n{retry_message}\n失败原因：{exc}"
                if isinstance(exc, CreativeBusinessValidationError):
                    if exc.retry_feedback:
                        retry_feedback += f"\n{exc.retry_feedback.strip()}"
                else:
                    retry_feedback += "\n" + (
                        retry_preamble.strip()
                        if retry_preamble is not None
                        else (
                            "shots 必须是数组；禁止重复 JSON key；"
                            "每个数组元素必须是独立对象。"
                        )
                    )
                if retry_instruction:
                    retry_feedback += f"\n{retry_instruction.strip()}"
                continue
        except requests.Timeout as exc:
            if task_logger:
                task_logger.api("LLM_FAILED", "DeepSeek", stage=raw_stage, error=exc)
            raise PromptGenerationError("DeepSeek 请求超时，请稍后重试。") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            detail = extract_api_error(exc.response) if exc.response is not None else ""
            suffix = f"：{detail}" if detail else ""
            if task_logger:
                task_logger.api(
                    "LLM_FAILED", "DeepSeek", stage=raw_stage, status=status, error=detail
                )
            raise PromptGenerationError(f"DeepSeek API 返回 HTTP {status}{suffix}") from exc
        except requests.RequestException as exc:
            if task_logger:
                task_logger.api("LLM_FAILED", "DeepSeek", stage=raw_stage, error=exc)
            raise PromptGenerationError(f"DeepSeek 网络请求失败：{exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            if task_logger:
                task_logger.error(exc, stage=raw_stage)
            raise PromptGenerationError(f"DeepSeek API 响应结构无效：{exc}") from exc
    if (
        creative_best_effort_callback is not None
        and len(duration_candidates) == MAX_JSON_REQUEST_ATTEMPTS
    ):
        selected = min(
            duration_candidates,
            key=lambda candidate: (
                candidate["duration_gap_seconds"],
                candidate["attempt"],
            ),
        )
        metadata = {
            key: selected[key]
            for key in (
                "attempt",
                "target_duration_seconds",
                "estimated_duration_seconds",
                "duration_gap_seconds",
            )
        }
        creative_best_effort_callback(metadata)
        message = (
            "Creative旁白时长连续3次未达到严格匹配要求，"
            "已选择最接近目标且不超出允许时长的合法候选。"
        )
        logger.warning(
            "%s selected_attempt=%s target=%.2fs estimated=%.2fs gap=%.2fs",
            message,
            metadata["attempt"],
            metadata["target_duration_seconds"],
            metadata["estimated_duration_seconds"],
            metadata["duration_gap_seconds"],
        )
        if task_logger:
            task_logger.event(
                "CREATIVE_NARRATION_DURATION_STRICT_VALIDATION_FAILED",
                "Creative Narration Duration strict validation: FAILED after 3 attempts",
                attempts=MAX_JSON_REQUEST_ATTEMPTS,
                **context_fields,
            )
            task_logger.event(
                "CREATIVE_NARRATION_BEST_EFFORT_FALLBACK_USED",
                "Best-effort fallback: USED",
                selected_attempt=metadata["attempt"],
                target_duration_seconds=metadata["target_duration_seconds"],
                estimated_duration_seconds=metadata["estimated_duration_seconds"],
                duration_gap_seconds=metadata["duration_gap_seconds"],
                **context_fields,
            )
            task_logger.api(
                "LLM_COMPLETED_BEST_EFFORT",
                "DeepSeek",
                stage=raw_stage,
                selected_attempt=metadata["attempt"],
                **context_fields,
            )
        return selected["data"]
    raise StructuredOutputExhaustedError(
        f"DeepSeek 连续 {MAX_JSON_REQUEST_ATTEMPTS} 次未返回可用结构化结果；"
        f"最后失败类型：{last_validation_category}；原因：{last_validation_error}"
    ) from last_validation_error


SAFETY_REVIEW_INSTRUCTION = """
你是品牌视频提示词的合规编辑。审核并改写输入，使其成为全年龄、温和、非暴力、非性化、无危险行为、无歧视、无违法内容的商业广告描述。消除可能被误解的身体细节、舔舐、裸露、攻击、受伤、武器、药物、未成年人敏感情境和过度拟人化表达，但不要规避平台审核，不改变产品身份和核心创意。保留主体、环境、动作、运镜、光线、色彩与广告质感，不添加对白、字幕或虚构卖点。
只输出 JSON：
{"is_safe":true,"risk_notes":[],"reviewed_video_prompt":"中性、安全、可直接提交的视频提示词"}
若本质上不适合安全改写，将 is_safe 设为 false，程序不会提交。
""".strip()


def review_prompt_safety(
    video_prompt: str,
    api_key: str,
    task_logger: TaskLogger | None = None,
    raw_stage: str = "prompt_safety",
) -> PromptSafetyReview:
    if not video_prompt.strip():
        raise PromptGenerationError("待审核的视频 Prompt 不能为空。")
    try:
        return PromptSafetyReview.model_validate(
            deepseek_json_request(
                api_key,
                SAFETY_REVIEW_INSTRUCTION,
                f"请审核并改写以下视频提示词，输出 JSON：\n{video_prompt}",
                max_tokens=1600,
                task_logger=task_logger,
                raw_stage=raw_stage,
            )
        )
    except ValidationError as exc:
        raise PromptGenerationError(f"DeepSeek 安全预检结果无效：{exc}") from exc
