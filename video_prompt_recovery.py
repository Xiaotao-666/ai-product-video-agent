"""Explicit, no-API recovery for a narrowly identifiable malformed prompt response."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus, now_iso
from prompt_generator import escape_illegal_control_characters, strip_markdown_fence
from storyboard import (
    Storyboard,
    VideoPromptPlan,
    _validate_prompt_plan,
    _validate_video_prompt_payload,
)
from task_logger import TaskLogger


class VideoPromptRecoveryError(RuntimeError):
    """Raised when a malformed response cannot be recovered without guessing."""


class _ObjectPairs(list[tuple[str, Any]]):
    """Marker type used to preserve duplicate JSON object keys and their order."""


def _preserve_pairs(pairs: list[tuple[str, Any]]) -> _ObjectPairs:
    return _ObjectPairs(pairs)


def recover_video_prompt_plan_from_raw(
    raw_content: str, board: Storyboard
) -> VideoPromptPlan:
    """Recover only the known alternating shot_id/video_prompt duplicate-key form.

    This function is deliberately not part of normal LLM parsing. Future malformed
    output must be rejected and retried instead of being inferred automatically.
    """
    cleaned = escape_illegal_control_characters(strip_markdown_fence(raw_content))
    try:
        root = json.loads(cleaned, object_pairs_hook=_preserve_pairs)
    except json.JSONDecodeError as exc:
        raise VideoPromptRecoveryError(f"原始响应不是可恢复的 JSON：{exc}") from exc

    if not isinstance(root, _ObjectPairs) or len(root) != 1 or root[0][0] != "shots":
        raise VideoPromptRecoveryError("原始响应顶层必须只包含一个 shots 字段。")
    raw_shots = root[0][1]
    if not isinstance(raw_shots, list) or len(raw_shots) != 1:
        raise VideoPromptRecoveryError("原始响应不符合已确认的单对象重复键形态。")
    shot_pairs = raw_shots[0]
    if not isinstance(shot_pairs, _ObjectPairs) or not shot_pairs:
        raise VideoPromptRecoveryError("原始 shots 内容无法保留为有序键值对。")

    recovered: list[dict[str, Any]] = []
    if len(shot_pairs) % 2 != 0:
        raise VideoPromptRecoveryError("重复键序列不是完整的 shot_id/video_prompt 配对。")
    for index in range(0, len(shot_pairs), 2):
        id_key, shot_id = shot_pairs[index]
        prompt_key, video_prompt = shot_pairs[index + 1]
        if id_key != "shot_id" or prompt_key != "video_prompt":
            raise VideoPromptRecoveryError(
                "重复键序列必须严格交替为 shot_id、video_prompt。"
            )
        recovered.append({"shot_id": shot_id, "video_prompt": video_prompt})

    payload = {"shots": recovered}
    _validate_video_prompt_payload(payload, board)
    plan = VideoPromptPlan.model_validate(payload, strict=True)
    return _validate_prompt_plan(plan, board)


def recover_project_video_prompts(
    project: ProjectPaths,
    raw_response_path: str | Path,
    task_logger: TaskLogger,
) -> VideoPromptPlan:
    """Restore video_prompts.json and its checkpoint without calling any API."""
    raw_path = project.ensure_within_project(raw_response_path)
    if not raw_path.is_file():
        raise VideoPromptRecoveryError(f"原始响应不存在：{raw_path}")

    try:
        board = Storyboard.model_validate(
            json.loads(project.storyboard_file_path().read_text(encoding="utf-8")),
            strict=True,
        )
        raw_content = raw_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise VideoPromptRecoveryError(f"恢复输入无法读取或验证：{exc}") from exc

    plan = recover_video_prompt_plan_from_raw(raw_content, board)
    expected_ids = [shot.shot_id for shot in board.shots]
    actual_ids = [shot.shot_id for shot in plan.shots]
    if actual_ids != expected_ids:
        raise VideoPromptRecoveryError(
            f"恢复结果与 Storyboard 不一致：expected={expected_ids}, actual={actual_ids}"
        )

    output_path = project.video_prompts_path()
    if output_path.exists():
        raise VideoPromptRecoveryError(f"正式 Video Prompt 已存在，拒绝覆盖：{output_path}")
    project.save_json(output_path, plan.model_dump())

    checkpoint = ProjectCheckpoint.load(project)
    timestamp = now_iso()
    video_prompt_stage = checkpoint.data["stages"][ProjectStage.VIDEO_PROMPT.value]
    video_prompt_stage["status"] = StageStatus.COMPLETED.value
    video_prompt_stage["completed_at"] = timestamp
    video_prompt_stage["updated_at"] = timestamp
    prompt_review_stage = checkpoint.data["stages"][ProjectStage.PROMPT_REVIEW.value]
    prompt_review_stage["status"] = StageStatus.WAITING_REVIEW.value
    prompt_review_stage["updated_at"] = timestamp
    checkpoint.data["current_stage"] = ProjectStage.PROMPT_REVIEW.value
    checkpoint.data["status"] = StageStatus.WAITING_REVIEW.value
    checkpoint.data["last_error"] = None
    checkpoint.save()

    task_logger.event(
        "VIDEO_PROMPT_RECOVERED_FROM_RAW",
        expected_shot_ids=expected_ids,
        actual_shot_ids=actual_ids,
        raw_response_path=raw_path,
        video_prompts_path=output_path,
    )
    return plan
