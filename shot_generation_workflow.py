"""Shared Core workflow for one initial Shot generation and safe manual resume."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, ShotStatus, StageStatus
from prompt_generator import PromptSafetyReview, review_prompt_safety
from shot_review import (
    active_prompt_payload,
    active_prompt_safety,
    save_safety_to_active_prompt,
)
from storyboard import StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_generation_request import ProviderSelection
from video_generator import (
    ProviderSubmissionUnknownError,
    generate_video,
)
from video_provider import VideoProviderError
from video_provider_registry import VideoProviderRegistry
from visual_input import visual_input_snapshot


class ShotGenerationWorkflowError(RuntimeError):
    pass


class InitialShotGenerationNotAllowed(ShotGenerationWorkflowError):
    pass


class ShotGenerationResumeUnavailable(ShotGenerationWorkflowError):
    pass


class ShotPromptSafetyRejected(ShotGenerationWorkflowError):
    pass


class ShotPromptSafetyUnavailable(ShotGenerationWorkflowError):
    pass


SafetyReview = Callable[[str, str, TaskLogger | None, str], PromptSafetyReview]
VideoGenerate = Callable[..., Path]


def _initial_state_is_valid(checkpoint: ProjectCheckpoint, shot_id: int) -> bool:
    entry = checkpoint.shot_checkpoint(shot_id)
    return (
        checkpoint.stage_status(ProjectStage.VIDEO_PROMPT) is StageStatus.COMPLETED
        and checkpoint.stage_status(ProjectStage.PROMPT_REVIEW) is StageStatus.APPROVED
        and checkpoint.shot_status(shot_id) is ShotStatus.NOT_STARTED
        and int(entry.get("generation_count") or 0) == 0
        and not entry.get("generation_versions")
        and entry.get("active_video_version") is None
        and entry.get("approved_video_version") is None
        and entry.get("pending_video_version") is None
        and entry.get("current_generation_version") is None
        and not entry.get("provider_task_id")
        and not entry.get("file_id")
        and int(entry.get("active_prompt_version") or 0) > 0
    )


def _generation_record(entry: Mapping[str, Any], version: int) -> Mapping[str, Any]:
    for value in reversed(list(entry.get("generation_versions") or [])):
        if isinstance(value, Mapping) and int(value.get("video_version") or 0) == version:
            return value
    return {}


def continue_shot_generation(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    shot_id: int,
    deepseek_key: str,
    provider_credentials: Mapping[str, Any] | str | None,
    task_logger: TaskLogger,
    provider_selection: ProviderSelection | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    visual_input: dict[str, Any] | None = None,
    safety: PromptSafetyReview | None = None,
    safety_review: SafetyReview = review_prompt_safety,
    video_generate: VideoGenerate = generate_video,
) -> Path:
    """Run or resume the provider-neutral Core path without any review action."""

    entry = checkpoint.shot_checkpoint(shot_id)
    version = int(
        entry.get("current_generation_version")
        or entry.get("pending_video_version")
        or 0
    )
    if version <= 0:
        raise ShotGenerationWorkflowError("Shot is missing its generation version.")

    output_path = paths.shot_version_video_path(shot_id, version)
    prompt_payload = active_prompt_payload(paths, checkpoint, plan, shot_id)
    resume_task = checkpoint.generation_provider_task(
        shot_id, entry.get("current_generation_version")
    )
    resuming = resume_task is not None
    generation_record = _generation_record(entry, version)
    recorded_prompt_version = generation_record.get("prompt_version")
    if resuming and recorded_prompt_version is not None and int(
        recorded_prompt_version
    ) != int(entry.get("active_prompt_version") or 0):
        raise ShotGenerationResumeUnavailable(
            "The active Prompt no longer matches the submitted generation."
        )

    generation_visual = visual_input_snapshot(
        checkpoint.generation_visual_input(shot_id, version)
        if resuming
        else visual_input or checkpoint.shot_visual_input(shot_id)
    )
    if safety is None:
        safety = active_prompt_safety(paths, checkpoint, plan, shot_id)
    if safety is None:
        if not str(deepseek_key or "").strip():
            raise ShotPromptSafetyUnavailable(
                "Prompt Safety is not configured and no saved result exists."
            )
        safety = safety_review(
            str(prompt_payload["prompt"]),
            deepseek_key,
            task_logger,
            f"prompt_safety_shot_{shot_id:02d}",
        )
        save_safety_to_active_prompt(paths, checkpoint, plan, shot_id, safety)
    if not safety.is_safe:
        error = ShotPromptSafetyRejected("Shot Prompt Safety did not pass.")
        checkpoint.mark_shot_failed(shot_id, error)
        raise error

    if output_path.is_file() and output_path.stat().st_size > 0:
        checkpoint.mark_shot_local_finalizing(shot_id)
        checkpoint.mark_shot_ready_for_review(shot_id)
        return output_path

    try:
        generated_path = video_generate(
            provider_credentials=provider_credentials,
            prompt=safety.reviewed_video_prompt,
            duration=shot.duration,
            resolution="768P",
            project=paths,
            output_path=output_path,
            task_logger=task_logger,
            shot_id=shot_id,
            visual_input=generation_visual,
            provider_selection=provider_selection,
            provider_registry=provider_registry,
            resume_task=resume_task,
            on_preflight=lambda metadata: checkpoint.mark_shot_preflight(
                shot_id, metadata
            ),
            on_submitting=lambda metadata: checkpoint.mark_shot_submission_started(
                shot_id,
                metadata,
                duration=shot.duration,
                resolution="768P",
                visual_input=generation_visual,
            ),
            on_submitted=lambda task: checkpoint.mark_shot_submitted(shot_id, task),
            on_task_updated=lambda task: checkpoint.mark_shot_task_updated(
                shot_id, task
            ),
            on_downloading=lambda _task: checkpoint.mark_shot_downloading(shot_id),
            on_downloaded=lambda _path: checkpoint.mark_shot_local_finalizing(
                shot_id
            ),
        )
    except ProviderSubmissionUnknownError:
        checkpoint.mark_shot_submission_unknown(shot_id)
        raise
    except (VideoProviderError, OSError, RuntimeError) as error:
        checkpoint.mark_shot_failed(shot_id, error)
        raise

    if not generated_path.is_file() or generated_path.stat().st_size <= 0:
        error = ShotGenerationWorkflowError(
            "Provider completed without a usable local video."
        )
        checkpoint.mark_shot_failed(shot_id, error)
        raise error
    checkpoint.mark_shot_local_finalizing(shot_id)
    checkpoint.mark_shot_ready_for_review(shot_id)
    return generated_path


def generate_initial_shot(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    shot_id: int,
    visual_input: dict[str, Any],
    deepseek_key: str,
    provider_credentials: Mapping[str, Any] | str | None,
    task_logger: TaskLogger,
    provider_selection: ProviderSelection | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    safety_review: SafetyReview = review_prompt_safety,
    video_generate: VideoGenerate = generate_video,
) -> Path:
    if not _initial_state_is_valid(checkpoint, shot_id):
        raise InitialShotGenerationNotAllowed(
            "Shot is not eligible for initial generation."
        )
    checkpoint.set_shot_visual_input(shot_id, visual_input_snapshot(visual_input))
    checkpoint.prepare_shot_generation(shot_id)
    return continue_shot_generation(
        paths=paths,
        checkpoint=checkpoint,
        plan=plan,
        shot=shot,
        shot_id=shot_id,
        deepseek_key=deepseek_key,
        provider_credentials=provider_credentials,
        task_logger=task_logger,
        provider_selection=provider_selection,
        provider_registry=provider_registry,
        safety_review=safety_review,
        video_generate=video_generate,
    )


def resume_shot_generation(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    shot_id: int,
    deepseek_key: str,
    provider_credentials: Mapping[str, Any] | str | None,
    task_logger: TaskLogger,
    provider_registry: VideoProviderRegistry | None = None,
    safety_review: SafetyReview = review_prompt_safety,
    video_generate: VideoGenerate = generate_video,
) -> Path:
    entry = checkpoint.shot_checkpoint(shot_id)
    if checkpoint.shot_status(shot_id) in {
        ShotStatus.WAITING_REVIEW,
        ShotStatus.APPROVED,
    }:
        raise ShotGenerationResumeUnavailable(
            "Shot review is already complete or awaiting a decision."
        )
    if bool(entry.get("submission_unknown")):
        raise ShotGenerationResumeUnavailable(
            "A submission with no known provider task cannot be resumed safely."
        )
    version = int(entry.get("current_generation_version") or 0)
    if version <= 0:
        raise ShotGenerationResumeUnavailable("No interrupted generation exists.")
    output = paths.shot_version_video_path(shot_id, version)
    provider_task = checkpoint.generation_provider_task(shot_id, version)
    if provider_task is None and not (output.is_file() and output.stat().st_size > 0):
        raise ShotGenerationResumeUnavailable("No durable provider progress exists.")
    return continue_shot_generation(
        paths=paths,
        checkpoint=checkpoint,
        plan=plan,
        shot=shot,
        shot_id=shot_id,
        deepseek_key=deepseek_key,
        provider_credentials=provider_credentials,
        task_logger=task_logger,
        provider_registry=provider_registry,
        safety_review=safety_review,
        video_generate=video_generate,
    )
