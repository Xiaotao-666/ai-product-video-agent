"""Application-neutral Video Prompt generation and persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from shot_review import ShotReviewError, ensure_initial_prompt_versions
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardError,
    VideoPromptPlan,
    _validate_prompt_plan,
    generate_video_prompts,
)
from task_logger import TaskLogger


class VideoPromptStageError(RuntimeError):
    """Base class for safe single-stage Video Prompt workflow failures."""


class VideoPromptStageStateError(VideoPromptStageError):
    """Raised when Video Prompt generation or recovery is not allowed."""


class VideoPromptStageDataError(VideoPromptStageError):
    """Raised when approved planning inputs cannot be loaded safely."""


class VideoPromptApprovalError(RuntimeError):
    """Raised when canonical Video Prompts cannot be approved safely."""


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


def _load_video_prompt_approval_inputs(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
) -> tuple[ProductVideoRequest, CreativeBrief, Storyboard, VideoPromptPlan]:
    try:
        request = ProductVideoRequest.model_validate(checkpoint.data["request"])
        brief = CreativeBrief.model_validate_json(
            paths.creative_brief_path().read_text(encoding="utf-8")
        )
        board = Storyboard.model_validate_json(
            paths.storyboard_file_path().read_text(encoding="utf-8")
        )
        plan = VideoPromptPlan.model_validate_json(
            paths.video_prompts_path().read_text(encoding="utf-8")
        )
    except (KeyError, OSError, UnicodeError, ValueError) as error:
        raise VideoPromptApprovalError(
            "Video Prompt approval inputs are unreadable"
        ) from error
    return request, brief, board, plan


def _require_existing_prompt_versions_match(
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
) -> None:
    """Reject stale pointers before approval makes any checkpoint write."""

    raw_shots = checkpoint.data.get("video_generation", {}).get("shots", {})
    if not isinstance(raw_shots, dict):
        raise VideoPromptApprovalError("Shot checkpoint data is invalid")
    for item in plan.shots:
        raw_entry = raw_shots.get(str(item.shot_id))
        if raw_entry is None:
            continue
        if not isinstance(raw_entry, dict):
            raise VideoPromptApprovalError("Shot checkpoint data is invalid")
        active = raw_entry.get("active_prompt_version")
        if active is None:
            continue
        if isinstance(active, bool):
            raise VideoPromptApprovalError("Active Prompt version is invalid")
        try:
            version = int(active)
        except (TypeError, ValueError, OverflowError) as error:
            raise VideoPromptApprovalError(
                "Active Prompt version is invalid"
            ) from error
        payload = checkpoint.prompt_version(item.shot_id, version)
        if (
            payload is None
            or str(payload.get("prompt") or "").strip()
            != item.video_prompt.strip()
        ):
            raise VideoPromptApprovalError(
                "Active Prompt version does not match canonical Video Prompt"
            )


def approve_video_prompts_stage(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    task_logger: TaskLogger | None = None,
) -> VideoPromptPlan:
    """Approve complete canonical Video Prompts without starting Shot generation.

    Approval initializes or preserves each Shot's ``active_prompt_version``.
    ``approved_prompt_version`` deliberately remains untouched: Core binds that
    pointer only when a concrete generated video version is approved.
    """

    if (
        checkpoint.stage_status(ProjectStage.VIDEO_PROMPT)
        is not StageStatus.COMPLETED
        or checkpoint.stage_status(ProjectStage.PROMPT_REVIEW)
        is not StageStatus.WAITING_REVIEW
        or checkpoint.stage_status(ProjectStage.VIDEO_GENERATION)
        is not StageStatus.NOT_STARTED
        or checkpoint.stage_status(ProjectStage.COMPLETED)
        is not StageStatus.NOT_STARTED
    ):
        raise VideoPromptApprovalError(
            "Video Prompts are not waiting for review"
        )

    request, brief, board, plan = _load_video_prompt_approval_inputs(
        paths, checkpoint
    )
    try:
        _validate_prompt_plan(
            plan,
            board,
            brief.global_constraints,
            request.product_name,
        )
    except StoryboardError as error:
        raise VideoPromptApprovalError(
            "Canonical Video Prompts are incomplete or invalid"
        ) from error

    _require_existing_prompt_versions_match(checkpoint, plan)
    checkpoint.ensure_shots([shot.shot_id for shot in board.shots])
    try:
        ensure_initial_prompt_versions(
            paths,
            checkpoint,
            plan,
            task_logger,
            persist_plan=False,
        )
    except ShotReviewError as error:
        raise VideoPromptApprovalError(
            "Video Prompt versions could not be initialized"
        ) from error

    # Verify the formal pointer against canonical content before committing
    # the human-review transition. Mixed per-Shot versions are preserved.
    for item in plan.shots:
        entry = checkpoint.shot_checkpoint(item.shot_id)
        active = entry.get("active_prompt_version")
        if active is None:
            raise VideoPromptApprovalError("Active Prompt version is missing")
        payload = checkpoint.prompt_version(item.shot_id, int(active))
        if (
            payload is None
            or str(payload.get("prompt") or "").strip()
            != item.video_prompt.strip()
        ):
            raise VideoPromptApprovalError(
                "Active Prompt version does not match canonical Video Prompt"
            )

    checkpoint.update_stage(ProjectStage.PROMPT_REVIEW, StageStatus.APPROVED)
    if task_logger is not None:
        task_logger.review_action("Prompt审核", "approve")
    return plan
