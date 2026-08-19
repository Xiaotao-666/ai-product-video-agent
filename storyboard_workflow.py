"""Application-neutral Storyboard generation and Core persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from storyboard import (
    CreativeBrief,
    Storyboard,
    generate_storyboard,
    revise_storyboard,
)
from task_logger import TaskLogger


class StoryboardStageError(RuntimeError):
    """Base class for safe single-stage Storyboard workflow failures."""


class StoryboardStageStateError(StoryboardStageError):
    """Raised when Storyboard generation is not allowed by Core state."""


class StoryboardStageDataError(StoryboardStageError):
    """Raised when the approved canonical Creative cannot be loaded."""


class StoryboardApprovalError(RuntimeError):
    """Raised when Core Storyboard state cannot be approved safely."""


def approve_storyboard_stage(checkpoint: ProjectCheckpoint) -> None:
    """Approve Storyboard review without generating Video Prompts.

    CLI and Web share this transition. Interactive review metadata remains the
    caller's responsibility: CLI records it through ``ReviewRecorder`` while
    the synchronous Web action creates neither a CLI review record nor a Web
    durable task.
    """

    if (
        checkpoint.stage_status(ProjectStage.STORYBOARD)
        != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW)
        != StageStatus.WAITING_REVIEW
    ):
        raise StoryboardApprovalError("Storyboard is not waiting for review")
    checkpoint.update_stage(
        ProjectStage.STORYBOARD_REVIEW,
        StageStatus.APPROVED,
    )


def _require_storyboard_generation_allowed(
    checkpoint: ProjectCheckpoint,
) -> None:
    if (
        checkpoint.stage_status(ProjectStage.CREATIVE) != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW)
        != StageStatus.APPROVED
        or checkpoint.stage_status(ProjectStage.STORYBOARD)
        != StageStatus.NOT_STARTED
        or checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW)
        != StageStatus.NOT_STARTED
    ):
        raise StoryboardStageStateError("Storyboard generation is not allowed")


def _load_approved_creative(paths: ProjectPaths) -> CreativeBrief:
    try:
        return CreativeBrief.model_validate_json(
            paths.creative_brief_path().read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise StoryboardStageDataError(
            "Approved Creative artifact is unreadable"
        ) from error


def load_storyboard(paths: ProjectPaths) -> Storyboard:
    """Load the canonical Storyboard through its strict Core schema."""

    try:
        return Storyboard.model_validate_json(
            paths.storyboard_file_path().read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise StoryboardStageDataError(
            "Storyboard artifact is unreadable"
        ) from error


def _require_storyboard_revision_allowed(
    checkpoint: ProjectCheckpoint,
) -> None:
    if (
        checkpoint.stage_status(ProjectStage.CREATIVE) != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW)
        != StageStatus.APPROVED
        or checkpoint.stage_status(ProjectStage.STORYBOARD)
        != StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW)
        != StageStatus.WAITING_REVIEW
    ):
        raise StoryboardStageStateError(
            "Storyboard revision is not allowed"
        )


def _record_storyboard_evaluation(
    recorder: EvaluationRecorder | None,
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    *,
    operation: str,
    reference_asset_context: dict[str, Any] | None,
    current: Storyboard | None = None,
    feedback: str | None = None,
) -> None:
    if recorder is None:
        return
    inputs: dict[str, Any] = {
        "product_information": request.model_dump(),
        "user_notes": request.user_notes,
        "reference_assets": reference_asset_context
        or {"available": False, "asset_count": 0},
        "creative_brief": brief.model_dump(),
    }
    if current is not None:
        inputs["current_output"] = current.model_dump()
    if feedback is not None:
        inputs["user_feedback"] = feedback
    recorder.record_prompt(
        "storyboard",
        model=DEEPSEEK_MODEL,
        operation=operation,
        input_fields=inputs,
        output_result=board.model_dump(),
    )


def _commit_storyboard_revision(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
) -> None:
    # The board is already provider-validated, Scheduler-compiled, and
    # constraint-validated. The Windows-safe atomic writer publishes it in one
    # replacement, so readers see either the old or the complete new board.
    paths.save_json(paths.storyboard_file_path(), board.model_dump())
    checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.STORYBOARD_REVIEW,
        StageStatus.WAITING_REVIEW,
    )


def generate_storyboard_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> Storyboard:
    """Generate exactly one Storyboard and stop at human review.

    ``generate_storyboard`` remains the sole owner of provider prompting,
    structured-output retry, deterministic timeline scheduling, and semantic
    validation. This callable owns the shared CLI/Web state and persistence
    transaction around that validated result.
    """

    _require_storyboard_generation_allowed(checkpoint)
    brief = _load_approved_creative(paths)
    checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.RUNNING)
    task_logger.set_stage("storyboard")
    visual_kwargs = (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )
    board = generate_storyboard(
        request,
        brief,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    if evaluation_recorder is not None:
        evaluation_recorder.record_prompt(
            "storyboard",
            model=DEEPSEEK_MODEL,
            operation="generate",
            input_fields={
                "product_information": request.model_dump(),
                "user_notes": request.user_notes,
                "reference_assets": reference_asset_context
                or {"available": False, "asset_count": 0},
                "creative_brief": brief.model_dump(),
            },
            output_result=board.model_dump(),
        )
    # Canonical replacement happens only after provider output, Scheduler, and
    # all Storyboard validation have completed successfully.
    paths.save_json(paths.storyboard_file_path(), board.model_dump())
    checkpoint.update_stage(ProjectStage.STORYBOARD, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.STORYBOARD_REVIEW,
        StageStatus.WAITING_REVIEW,
    )
    task_logger.event("STORYBOARD_GENERATED", "Storyboard 生成成功")
    return board


def revise_storyboard_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    current: Storyboard,
    feedback: str,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    approved_creative: CreativeBrief | None = None,
    evaluation_recorder: EvaluationRecorder | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> Storyboard:
    """Revise canonical Storyboard and return to human review.

    Provider prompting, semantic parsing, Scheduler compilation, and all hard
    validation remain owned by ``storyboard.revise_storyboard``. Canonical
    files are untouched until those steps have succeeded.
    """

    _require_storyboard_revision_allowed(checkpoint)
    normalized_feedback = str(feedback).strip()
    if not normalized_feedback:
        raise StoryboardStageStateError("Storyboard feedback is required")
    brief = approved_creative or _load_approved_creative(paths)
    original_brief = brief.model_dump()
    task_logger.set_stage("storyboard")
    visual_kwargs: dict[str, Any] = {}
    if visual_analysis_result is not None:
        visual_kwargs["visual_analysis_result"] = visual_analysis_result
    if visual_constraints is not None:
        visual_kwargs["visual_constraints"] = visual_constraints
    if reference_asset_context:
        visual_kwargs["reference_asset_context"] = reference_asset_context
    board = revise_storyboard(
        request,
        brief,
        current,
        normalized_feedback,
        deepseek_key,
        task_logger,
        persist_creative=None,
        **visual_kwargs,
    )
    _record_storyboard_evaluation(
        evaluation_recorder,
        request,
        brief,
        board,
        operation="revise",
        reference_asset_context=reference_asset_context,
        current=current,
        feedback=normalized_feedback,
    )

    updated_brief = brief.model_dump()
    brief_changed = updated_brief != original_brief
    if brief_changed:
        paths.save_json(paths.creative_brief_path(), updated_brief)
    try:
        _commit_storyboard_revision(paths, checkpoint, board)
    except Exception:
        # A feedback-derived AV constraint is committed only together with its
        # validated Storyboard. Restore Creative if the Storyboard publication
        # itself fails; the old canonical Storyboard remains atomic and intact.
        if brief_changed:
            paths.save_json(paths.creative_brief_path(), original_brief)
        raise
    task_logger.event("STORYBOARD_REVISED", "Storyboard 修改成功")
    return board


def regenerate_storyboard_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    approved_creative: CreativeBrief | None = None,
    evaluation_recorder: EvaluationRecorder | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> Storyboard:
    """Generate a clean Storyboard replacement and return to human review."""

    _require_storyboard_revision_allowed(checkpoint)
    # A review action requires a valid current canonical even though clean
    # regeneration deliberately does not pass it to the provider.
    load_storyboard(paths)
    brief = approved_creative or _load_approved_creative(paths)
    task_logger.set_stage("storyboard")
    visual_kwargs: dict[str, Any] = {}
    if visual_analysis_result is not None:
        visual_kwargs["visual_analysis_result"] = visual_analysis_result
    if visual_constraints is not None:
        visual_kwargs["visual_constraints"] = visual_constraints
    if reference_asset_context:
        visual_kwargs["reference_asset_context"] = reference_asset_context
    board = generate_storyboard(
        request,
        brief,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    _record_storyboard_evaluation(
        evaluation_recorder,
        request,
        brief,
        board,
        operation="regenerate",
        reference_asset_context=reference_asset_context,
    )
    _commit_storyboard_revision(paths, checkpoint, board)
    task_logger.event(
        "STORYBOARD_REGENERATED",
        "Storyboard 重新生成成功",
    )
    return board
