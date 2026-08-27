"""Shared Core workflow for one initial Shot generation and safe manual resume."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from project_manager import ProjectPaths
from project_state import (
    CandidateStatus,
    ProjectCheckpoint,
    ProjectStage,
    ShotStatus,
    StageStatus,
)
from prompt_generator import PromptSafetyReview, review_prompt_safety
from shot_review import (
    ShotReviewError,
    active_prompt_payload,
    active_prompt_safety,
    create_manual_prompt_version,
    save_safety_to_active_prompt,
)
from storyboard import (
    CreativeBrief,
    StoryboardShot,
    VideoPromptPlan,
    VideoPromptStructureError,
)
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


class CurrentPromptRegenerationNotAllowed(ShotGenerationWorkflowError):
    pass


class ManualPromptRegenerationNotAllowed(ShotGenerationWorkflowError):
    pass


class SelectedPromptVersionGenerationNotAllowed(ShotGenerationWorkflowError):
    pass


class GenerationIntent(StrEnum):
    INITIAL_GENERATION = "INITIAL_GENERATION"
    REGENERATE_CURRENT_PROMPT = "REGENERATE_CURRENT_PROMPT"
    REGENERATE_MANUAL_PROMPT = "REGENERATE_MANUAL_PROMPT"
    GENERATE_WITH_PROMPT_VERSION = "GENERATE_WITH_PROMPT_VERSION"


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
    candidate_lane: bool = False,
    resolution: str = "768P",
) -> Path:
    """Run or resume the provider-neutral Core path without any review action."""

    entry = checkpoint.shot_checkpoint(shot_id)
    candidate = checkpoint.candidate_checkpoint(shot_id)
    version = int(
        candidate.get("video_version")
        if candidate_lane
        else entry.get("current_generation_version")
        or entry.get("pending_video_version")
        or 0
    )
    if version <= 0:
        raise ShotGenerationWorkflowError("Shot is missing its generation version.")

    output_path = paths.shot_version_video_path(shot_id, version)
    prompt_payload = (
        checkpoint.prompt_version(shot_id, int(candidate.get("prompt_version") or 0))
        if candidate_lane
        else active_prompt_payload(paths, checkpoint, plan, shot_id)
    )
    if not isinstance(prompt_payload, dict):
        raise ShotGenerationWorkflowError("Shot is missing its Prompt snapshot.")
    resume_task = checkpoint.generation_provider_task(
        shot_id,
        candidate.get("video_version")
        if candidate_lane
        else entry.get("current_generation_version"),
    )
    resuming = resume_task is not None
    generation_record = _generation_record(entry, version)
    # A resumed attempt owns its configuration. Caller/UI defaults must never
    # change the request that was already submitted; legacy attempts used 768P.
    generation_duration = (
        int(generation_record.get("duration") or shot.duration) if resuming else shot.duration
    )
    generation_resolution = (
        str(generation_record.get("resolution") or "768P") if resuming else resolution
    )
    if resuming and isinstance(generation_record.get("prompt_snapshot"), Mapping):
        prompt_payload = dict(generation_record["prompt_snapshot"])
    recorded_prompt_version = generation_record.get("prompt_version")
    expected_prompt_version = int(
        candidate.get("prompt_version")
        if candidate_lane
        else entry.get("active_prompt_version")
        or 0
    )
    if resuming and recorded_prompt_version is not None and int(
        recorded_prompt_version
    ) != expected_prompt_version:
        raise ShotGenerationResumeUnavailable(
            "The active Prompt no longer matches the submitted generation."
        )

    generation_visual = visual_input_snapshot(
        checkpoint.generation_visual_input(shot_id, version)
        if resuming
        else visual_input or checkpoint.shot_visual_input(shot_id)
    )
    if safety is None and candidate_lane:
        if isinstance(prompt_payload.get("safety_is_safe"), bool):
            safety = PromptSafetyReview(
                is_safe=prompt_payload["safety_is_safe"],
                reviewed_video_prompt=str(
                    prompt_payload.get("safety_prompt")
                    or prompt_payload.get("prompt")
                    or ""
                ),
                risk_notes=list(prompt_payload.get("safety_risk_notes") or []),
            )
    if safety is None and not candidate_lane:
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
        if candidate_lane:
            prompt_payload = dict(prompt_payload)
            prompt_payload["safety_prompt"] = safety.reviewed_video_prompt
            prompt_payload["safety_is_safe"] = safety.is_safe
            prompt_payload["safety_risk_notes"] = list(safety.risk_notes)
        else:
            save_safety_to_active_prompt(paths, checkpoint, plan, shot_id, safety)
    if not safety.is_safe:
        error = ShotPromptSafetyRejected("Shot Prompt Safety did not pass.")
        (
            checkpoint.mark_candidate_failed(shot_id, error)
            if candidate_lane
            else checkpoint.mark_shot_failed(shot_id, error)
        )
        raise error

    if output_path.is_file() and output_path.stat().st_size > 0:
        if candidate_lane:
            checkpoint.mark_candidate_local_finalizing(shot_id)
            checkpoint.mark_candidate_ready(shot_id)
        else:
            checkpoint.mark_shot_local_finalizing(shot_id)
            checkpoint.mark_shot_ready_for_review(shot_id)
        return output_path

    try:
        generated_path = video_generate(
            provider_credentials=provider_credentials,
            prompt=safety.reviewed_video_prompt,
            duration=generation_duration,
            resolution=generation_resolution,
            project=paths,
            output_path=output_path,
            task_logger=task_logger,
            shot_id=shot_id,
            visual_input=generation_visual,
            provider_selection=provider_selection,
            provider_registry=provider_registry,
            resume_task=resume_task,
            on_preflight=(
                lambda metadata: checkpoint.mark_candidate_preflight(shot_id, metadata)
                if candidate_lane
                else checkpoint.mark_shot_preflight(shot_id, metadata)
            ),
            on_submitting=(
                lambda metadata: checkpoint.mark_candidate_submission_started(
                    shot_id,
                    metadata,
                    duration=generation_duration,
                    resolution=generation_resolution,
                    visual_input=generation_visual,
                    prompt_snapshot=dict(prompt_payload),
                )
                if candidate_lane
                else checkpoint.mark_shot_submission_started(
                    shot_id,
                    metadata,
                    duration=generation_duration,
                    resolution=generation_resolution,
                    visual_input=generation_visual,
                )
            ),
            on_submitted=(
                lambda task: checkpoint.mark_candidate_submitted(
                    shot_id,
                    task,
                    prompt_snapshot=dict(prompt_payload),
                )
                if candidate_lane
                else checkpoint.mark_shot_submitted(shot_id, task)
            ),
            on_task_updated=(
                lambda task: checkpoint.mark_candidate_task_updated(shot_id, task)
                if candidate_lane
                else checkpoint.mark_shot_task_updated(shot_id, task)
            ),
            on_downloading=(
                lambda _task: checkpoint.mark_candidate_downloading(shot_id)
                if candidate_lane
                else checkpoint.mark_shot_downloading(shot_id)
            ),
            on_downloaded=(
                lambda _path: checkpoint.mark_candidate_local_finalizing(shot_id)
                if candidate_lane
                else checkpoint.mark_shot_local_finalizing(shot_id)
            ),
        )
    except ProviderSubmissionUnknownError:
        (
            checkpoint.mark_candidate_submission_unknown(shot_id)
            if candidate_lane
            else checkpoint.mark_shot_submission_unknown(shot_id)
        )
        raise
    except (VideoProviderError, OSError, RuntimeError) as error:
        (
            checkpoint.mark_candidate_failed(shot_id, error)
            if candidate_lane
            else checkpoint.mark_shot_failed(shot_id, error)
        )
        raise

    if not generated_path.is_file() or generated_path.stat().st_size <= 0:
        error = ShotGenerationWorkflowError(
            "Provider completed without a usable local video."
        )
        (
            checkpoint.mark_candidate_failed(shot_id, error)
            if candidate_lane
            else checkpoint.mark_shot_failed(shot_id, error)
        )
        raise error
    if candidate_lane:
        checkpoint.mark_candidate_local_finalizing(shot_id)
        checkpoint.mark_candidate_ready(shot_id)
    else:
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
    checkpoint.prepare_shot_generation(
        shot_id, generation_intent=GenerationIntent.INITIAL_GENERATION.value
    )
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


def regenerate_shot_with_current_prompt(
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
    """Create one new Video version while preserving the current Prompt version."""

    return _regenerate_shot_with_prompt(
        paths=paths,
        checkpoint=checkpoint,
        plan=plan,
        shot=shot,
        shot_id=shot_id,
        visual_input=visual_input,
        deepseek_key=deepseek_key,
        provider_credentials=provider_credentials,
        task_logger=task_logger,
        provider_selection=provider_selection,
        provider_registry=provider_registry,
        safety_review=safety_review,
        video_generate=video_generate,
        prompt_version_override=None,
        generation_intent=GenerationIntent.REGENERATE_CURRENT_PROMPT,
        candidate_source="same_prompt",
    )


def regenerate_shot_with_prompt_version(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    shot_id: int,
    target_prompt_version: int,
    visual_input: dict[str, Any],
    deepseek_key: str,
    provider_credentials: Mapping[str, Any] | str | None,
    task_logger: TaskLogger,
    provider_selection: ProviderSelection | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    safety_review: SafetyReview = review_prompt_safety,
    video_generate: VideoGenerate = generate_video,
) -> Path:
    """Generate one Video bound to an existing adopted AI Prompt Version."""

    prompt_payload = checkpoint.prompt_version(shot_id, int(target_prompt_version))
    if (
        not isinstance(prompt_payload, dict)
        or str(prompt_payload.get("prompt") or "").strip() == ""
        or str(prompt_payload.get("source") or "").strip().lower() != "ai_revision"
    ):
        raise SelectedPromptVersionGenerationNotAllowed(
            "The selected adopted AI Prompt Version is unavailable."
        )
    entry = checkpoint.shot_checkpoint(shot_id)
    if (
        entry.get("approved_video_version") is None
        and int(entry.get("active_prompt_version") or 0)
        != int(target_prompt_version)
    ):
        raise SelectedPromptVersionGenerationNotAllowed(
            "An unapproved Shot can only generate from its active adopted Prompt Version."
        )
    return _regenerate_shot_with_prompt(
        paths=paths,
        checkpoint=checkpoint,
        plan=plan,
        shot=shot,
        shot_id=shot_id,
        visual_input=visual_input,
        deepseek_key=deepseek_key,
        provider_credentials=provider_credentials,
        task_logger=task_logger,
        provider_selection=provider_selection,
        provider_registry=provider_registry,
        safety_review=safety_review,
        video_generate=video_generate,
        prompt_version_override=int(target_prompt_version),
        generation_intent=GenerationIntent.GENERATE_WITH_PROMPT_VERSION,
        candidate_source="adopted_ai_revision_prompt",
    )


def regenerate_shot_with_manual_prompt(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    brief: CreativeBrief,
    shot: StoryboardShot,
    shot_id: int,
    base_prompt_version: int,
    edited_visual_prompt_core: str,
    visual_input: dict[str, Any],
    deepseek_key: str,
    provider_credentials: Mapping[str, Any] | str | None,
    task_logger: TaskLogger,
    product_name: str | None = None,
    provider_selection: ProviderSelection | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    safety_review: SafetyReview = review_prompt_safety,
    video_generate: VideoGenerate = generate_video,
) -> Path:
    """Persist a new manual Prompt and generate one Video bound to its snapshot."""

    entry = checkpoint.shot_checkpoint(shot_id)
    approved_video = entry.get("approved_video_version")
    candidate_status = checkpoint.candidate_status(shot_id)
    allowed = (
        checkpoint.shot_status(shot_id) is ShotStatus.WAITING_REVIEW
        and approved_video is None
        and int(entry.get("active_video_version") or 0) > 0
    ) or (
        checkpoint.shot_status(shot_id) is ShotStatus.APPROVED
        and approved_video is not None
        and candidate_status is not CandidateStatus.GENERATING
        and not bool(checkpoint.candidate_checkpoint(shot_id).get("submission_unknown"))
        and str(
            checkpoint.candidate_checkpoint(shot_id).get("generation_phase") or ""
        ).upper()
        != "SUBMISSION_UNKNOWN"
    )
    if not allowed:
        raise ManualPromptRegenerationNotAllowed(
            "The Shot is not eligible for manual Prompt regeneration."
        )
    try:
        prompt_payload = create_manual_prompt_version(
            paths=paths,
            checkpoint=checkpoint,
            plan=plan,
            shot=shot,
            shot_id=shot_id,
            base_prompt_version=base_prompt_version,
            edited_visual_prompt_core=edited_visual_prompt_core,
            task_logger=task_logger,
            global_constraints=brief.global_constraints,
            product_name=product_name,
        )
    except (ShotReviewError, VideoPromptStructureError) as error:
        raise ManualPromptRegenerationNotAllowed(str(error)) from error
    return _regenerate_shot_with_prompt(
        paths=paths,
        checkpoint=checkpoint,
        plan=plan,
        shot=shot,
        shot_id=shot_id,
        visual_input=visual_input,
        deepseek_key=deepseek_key,
        provider_credentials=provider_credentials,
        task_logger=task_logger,
        provider_selection=provider_selection,
        provider_registry=provider_registry,
        safety_review=safety_review,
        video_generate=video_generate,
        prompt_version_override=int(prompt_payload["version"]),
        generation_intent=GenerationIntent.REGENERATE_MANUAL_PROMPT,
        candidate_source="manual_prompt",
    )


def _regenerate_shot_with_prompt(
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
    prompt_version_override: int | None,
    generation_intent: GenerationIntent,
    candidate_source: str,
) -> Path:
    entry = checkpoint.shot_checkpoint(shot_id)
    status = checkpoint.shot_status(shot_id)
    approved_video = entry.get("approved_video_version")
    candidate_lane = approved_video is not None
    visual = visual_input_snapshot(visual_input)

    if not candidate_lane:
        if status is not ShotStatus.WAITING_REVIEW:
            raise CurrentPromptRegenerationNotAllowed(
                "Only an unapproved review version can be regenerated here."
            )
        previous_version = int(entry.get("active_video_version") or 0)
        if previous_version <= 0:
            raise CurrentPromptRegenerationNotAllowed(
                "The Shot has no current review version."
            )
        previous = checkpoint._generation_for_version(entry, previous_version)
        if previous is not None:
            previous.update(
                {
                    "status": "REJECTED",
                    "review_result": "REJECTED",
                    "is_active": False,
                }
            )
            from shot_storage import write_review_snapshot

            write_review_snapshot(
                paths,
                shot_id,
                previous_version,
                review_result="REJECTED",
                user_action="regenerate_current_prompt",
            )
        entry["visual_input"] = visual
        entry["visual_input_selected"] = True
        checkpoint.prepare_shot_generation(
            shot_id,
            generation_intent=generation_intent.value,
        )
    else:
        if status is not ShotStatus.APPROVED:
            raise CurrentPromptRegenerationNotAllowed(
                "The official Shot is not approved."
            )
        candidate = checkpoint.candidate_checkpoint(shot_id)
        candidate_status = checkpoint.candidate_status(shot_id)
        if (
            candidate_status is CandidateStatus.GENERATING
            or bool(candidate.get("submission_unknown"))
            or str(candidate.get("generation_phase") or "").upper()
            == "SUBMISSION_UNKNOWN"
        ):
            raise CurrentPromptRegenerationNotAllowed(
                "A pending generation is already in progress."
            )
        if candidate_status is not CandidateStatus.NONE:
            old_version = candidate.get("video_version")
            archived = dict(candidate)
            archived["result"] = "REJECTED"
            archived["finished_at"] = archived.get("updated_at")
            entry.setdefault("candidate_history", []).append(archived)
            if old_version is not None:
                old_generation = checkpoint._generation_for_version(
                    entry, int(old_version)
                )
                if old_generation is not None:
                    old_generation.update(
                        {
                            "status": "REJECTED",
                            "review_result": "REJECTED",
                            "is_active": False,
                            "is_approved": False,
                        }
                    )
                from shot_storage import write_review_snapshot

                write_review_snapshot(
                    paths,
                    shot_id,
                    int(old_version),
                    review_result="REJECTED",
                    user_action="regenerate_current_prompt",
                )
        prompt_version = int(
            prompt_version_override
            or candidate.get("prompt_version")
            or entry.get("approved_prompt_version")
            or entry.get("active_prompt_version")
            or 0
        )
        if prompt_version <= 0 or checkpoint.prompt_version(shot_id, prompt_version) is None:
            raise CurrentPromptRegenerationNotAllowed(
                "The current official Prompt is unavailable."
            )
        fresh = checkpoint._new_candidate_entry()
        fresh.update(
            {
                "status": CandidateStatus.EDITING.value,
                "base_approved_prompt_version": entry.get("approved_prompt_version"),
                "base_approved_video_version": entry.get("approved_video_version"),
                "prompt_version": prompt_version,
                "visual_input": visual,
                "source": candidate_source,
            }
        )
        entry["candidate"] = fresh
        checkpoint.prepare_candidate_generation(
            shot_id,
            generation_intent=generation_intent.value,
        )

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
        visual_input=visual,
        safety_review=safety_review,
        video_generate=video_generate,
        candidate_lane=candidate_lane,
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
    candidate = checkpoint.candidate_checkpoint(shot_id)
    candidate_version = int(candidate.get("video_version") or 0)
    candidate_lane = (
        candidate_version > 0
        and str(candidate.get("generation_intent") or "")
        in {
            GenerationIntent.REGENERATE_CURRENT_PROMPT.value,
            GenerationIntent.REGENERATE_MANUAL_PROMPT.value,
            GenerationIntent.GENERATE_WITH_PROMPT_VERSION.value,
        }
        and checkpoint.candidate_status(shot_id)
        in {CandidateStatus.GENERATING, CandidateStatus.FAILED}
    )
    if not candidate_lane and checkpoint.shot_status(shot_id) in {
        ShotStatus.WAITING_REVIEW,
        ShotStatus.APPROVED,
    }:
        raise ShotGenerationResumeUnavailable(
            "Shot review is already complete or awaiting a decision."
        )
    if bool(candidate.get("submission_unknown") if candidate_lane else entry.get("submission_unknown")):
        raise ShotGenerationResumeUnavailable(
            "A submission with no known provider task cannot be resumed safely."
        )
    version = candidate_version if candidate_lane else int(entry.get("current_generation_version") or 0)
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
        candidate_lane=candidate_lane,
    )
