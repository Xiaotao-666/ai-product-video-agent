"""Application-neutral Storyboard generation and Core persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from storyboard import CreativeBrief, Storyboard, generate_storyboard
from task_logger import TaskLogger


class StoryboardStageError(RuntimeError):
    """Base class for safe single-stage Storyboard workflow failures."""


class StoryboardStageStateError(StoryboardStageError):
    """Raised when Storyboard generation is not allowed by Core state."""


class StoryboardStageDataError(StoryboardStageError):
    """Raised when the approved canonical Creative cannot be loaded."""


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
