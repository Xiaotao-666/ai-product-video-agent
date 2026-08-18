"""Application-neutral Creative generation and Core persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from pydantic import ValidationError

from storyboard import (
    CreativeBrief,
    generate_creative_brief,
    revise_creative_brief,
)
from task_logger import TaskLogger


class CreativeApprovalError(RuntimeError):
    """Raised when Core Creative state cannot be approved safely."""


class CreativeRevisionError(RuntimeError):
    """Raised when a persisted Creative cannot be revised safely."""


def approve_creative_stage(checkpoint: ProjectCheckpoint) -> None:
    """Approve the persisted Creative review without starting Storyboard.

    CLI and Web both use this callable so the durable checkpoint transition
    remains owned by Core. Review interaction records remain the caller's
    responsibility because CLI records an interactive task while Web approval
    is a short synchronous action with no task record.
    """

    if (
        checkpoint.stage_status(ProjectStage.CREATIVE) != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW)
        != StageStatus.WAITING_REVIEW
    ):
        raise CreativeApprovalError("Creative is not waiting for review")
    checkpoint.update_stage(
        ProjectStage.CREATIVE_REVIEW,
        StageStatus.APPROVED,
    )


def generate_creative_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    """Generate exactly one Creative artifact and advance to human review.

    Provider prompting, structured-output validation, and format retries remain
    owned by ``generate_creative_brief``. This callable owns the same Core
    persistence and checkpoint transitions for both CLI and Web entry points.
    """

    checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.RUNNING)
    task_logger.set_stage("creative")
    visual_kwargs = (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )
    brief = generate_creative_brief(
        request,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    if evaluation_recorder is not None:
        evaluation_recorder.record_prompt(
            "creative",
            model=DEEPSEEK_MODEL,
            operation="generate",
            input_fields={
                "product_information": request.model_dump(),
                "user_notes": request.user_notes,
                "reference_assets": reference_asset_context
                or {"available": False, "asset_count": 0},
            },
            output_result=brief.model_dump(),
        )
    paths.save_json(paths.creative_brief_path(), brief.model_dump())
    checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.CREATIVE_REVIEW,
        StageStatus.WAITING_REVIEW,
    )
    task_logger.event("CREATIVE_GENERATED", "创意方案生成成功")
    return brief


def load_creative_brief(paths: ProjectPaths) -> CreativeBrief:
    """Load the canonical Creative artifact through its Core schema."""

    try:
        payload = paths.creative_brief_path().read_text(encoding="utf-8")
        return CreativeBrief.model_validate_json(payload)
    except (OSError, UnicodeError, ValidationError) as error:
        raise CreativeRevisionError("Creative artifact is unreadable") from error


def _require_creative_revision_allowed(checkpoint: ProjectCheckpoint) -> None:
    if (
        checkpoint.stage_status(ProjectStage.CREATIVE) != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW)
        != StageStatus.WAITING_REVIEW
    ):
        raise CreativeRevisionError("Creative is not waiting for review")


def _record_creative_revision(
    recorder: EvaluationRecorder | None,
    request: ProductVideoRequest,
    output: CreativeBrief,
    *,
    operation: str,
    reference_asset_context: dict[str, Any] | None,
    current: CreativeBrief | None = None,
    feedback: str | None = None,
) -> None:
    if recorder is None:
        return
    extra_inputs: dict[str, Any] = {}
    if current is not None:
        extra_inputs["current_output"] = current.model_dump()
    if feedback is not None:
        extra_inputs["user_feedback"] = feedback
    recorder.record_prompt(
        "creative",
        model=DEEPSEEK_MODEL,
        operation=operation,
        input_fields={
            "product_information": request.model_dump(),
            "user_notes": request.user_notes,
            "reference_assets": reference_asset_context
            or {"available": False, "asset_count": 0},
            **extra_inputs,
        },
        output_result=output.model_dump(),
    )


def _replace_creative_after_validation(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    brief: CreativeBrief,
) -> None:
    """Atomically replace canonical Creative only after provider validation."""

    paths.save_json(paths.creative_brief_path(), brief.model_dump())
    checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.CREATIVE_REVIEW,
        StageStatus.WAITING_REVIEW,
    )


def revise_creative_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    current: CreativeBrief,
    feedback: str,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    """Revise the current Creative with feedback and preserve it on failure."""

    _require_creative_revision_allowed(checkpoint)
    normalized_feedback = str(feedback).strip()
    if not normalized_feedback:
        raise CreativeRevisionError("Creative revision feedback is required")
    task_logger.set_stage("creative")
    visual_kwargs = (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )
    revised = revise_creative_brief(
        request,
        current,
        normalized_feedback,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    _record_creative_revision(
        evaluation_recorder,
        request,
        revised,
        operation="revise",
        reference_asset_context=reference_asset_context,
        current=current,
        feedback=normalized_feedback,
    )
    _replace_creative_after_validation(paths, checkpoint, revised)
    task_logger.event("CREATIVE_REVISED", "创意方案修改成功")
    return revised


def regenerate_creative_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    """Generate a fresh Creative from the original request without feedback."""

    _require_creative_revision_allowed(checkpoint)
    # Loading validates that an old canonical Creative really exists. It is not
    # included in the regenerate prompt and remains untouched until success.
    load_creative_brief(paths)
    task_logger.set_stage("creative")
    visual_kwargs = (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )
    regenerated = generate_creative_brief(
        request,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    _record_creative_revision(
        evaluation_recorder,
        request,
        regenerated,
        operation="regenerate",
        reference_asset_context=reference_asset_context,
    )
    _replace_creative_after_validation(paths, checkpoint, regenerated)
    task_logger.event("CREATIVE_REGENERATED", "创意方案重新生成成功")
    return regenerated
