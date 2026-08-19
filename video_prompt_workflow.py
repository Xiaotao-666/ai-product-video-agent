"""Application-neutral Video Prompt generation and persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from storyboard import (
    CreativeBrief,
    Storyboard,
    VideoPromptPlan,
    generate_video_prompts,
)
from task_logger import TaskLogger


class VideoPromptStageError(RuntimeError):
    """Base class for safe single-stage Video Prompt workflow failures."""


class VideoPromptStageStateError(VideoPromptStageError):
    """Raised when Video Prompt generation or recovery is not allowed."""


class VideoPromptStageDataError(VideoPromptStageError):
    """Raised when approved planning inputs cannot be loaded safely."""


_RESUMABLE_VIDEO_PROMPT_STATUSES = {
    StageStatus.NOT_STARTED,
    StageStatus.RUNNING,
    StageStatus.FAILED,
}


def _require_video_prompt_generation_allowed(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
) -> None:
    prompt_status = checkpoint.stage_status(ProjectStage.VIDEO_PROMPT)
    if (
        checkpoint.stage_status(ProjectStage.CREATIVE) is not StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW)
        is not StageStatus.APPROVED
        or checkpoint.stage_status(ProjectStage.STORYBOARD)
        is not StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW)
        is not StageStatus.APPROVED
        or prompt_status not in _RESUMABLE_VIDEO_PROMPT_STATUSES
        or checkpoint.stage_status(ProjectStage.PROMPT_REVIEW)
        is not StageStatus.NOT_STARTED
        or checkpoint.stage_status(ProjectStage.VIDEO_GENERATION)
        is not StageStatus.NOT_STARTED
        or checkpoint.stage_status(ProjectStage.COMPLETED)
        is not StageStatus.NOT_STARTED
        or paths.video_prompts_path().exists()
    ):
        raise VideoPromptStageStateError(
            "Video Prompt generation is not allowed"
        )
    if (
        prompt_status in {StageStatus.RUNNING, StageStatus.FAILED}
        and checkpoint.current_stage is not ProjectStage.VIDEO_PROMPT
    ):
        raise VideoPromptStageStateError(
            "Video Prompt recovery requires the Video Prompt stage"
        )


def _load_planning_inputs(
    paths: ProjectPaths,
) -> tuple[CreativeBrief, Storyboard]:
    try:
        brief = CreativeBrief.model_validate_json(
            paths.creative_brief_path().read_text(encoding="utf-8")
        )
        board = Storyboard.model_validate_json(
            paths.storyboard_file_path().read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise VideoPromptStageDataError(
            "Approved Creative or Storyboard artifact is unreadable"
        ) from error
    return brief, board


def generate_video_prompts_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> VideoPromptPlan:
    """Generate or explicitly resume per-Shot prompts and stop at review.

    ``storyboard.generate_video_prompts`` remains the sole owner of the
    per-Shot provider loop, strict JSON retries, fingerprinted progress cache,
    deterministic control blocks, and final plan validation. This callable
    owns the shared CLI/Web state and canonical publication boundary.
    """

    _require_video_prompt_generation_allowed(paths, checkpoint)
    brief, board = _load_planning_inputs(paths)
    checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.RUNNING)
    task_logger.set_stage("video_prompt")
    plan = generate_video_prompts(
        request,
        brief,
        board,
        deepseek_key,
        task_logger,
        reference_asset_context=reference_asset_context,
        progress_path=paths.video_prompt_generation_progress_path(),
    )
    if evaluation_recorder is not None:
        evaluation_recorder.record_prompt(
            "video_prompt",
            model=DEEPSEEK_MODEL,
            operation="generate",
            input_fields={
                "product_information": request.model_dump(),
                "user_notes": request.user_notes,
                "reference_assets": reference_asset_context
                or {"available": False, "asset_count": 0},
                "creative_brief": brief.model_dump(),
                "storyboard": board.model_dump(),
            },
            output_result=plan.model_dump(),
        )

    # Publish canonical content only after every Shot and the complete plan
    # have passed Core validation. The ProjectPaths writer is atomic.
    paths.save_json(paths.video_prompts_path(), plan.model_dump())
    checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.PROMPT_REVIEW,
        StageStatus.WAITING_REVIEW,
    )
    task_logger.event("PROMPT_GENERATED", "Video Prompt 生成完成")
    return plan
