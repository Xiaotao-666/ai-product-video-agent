"""Shared Core validation and persistence for approving one Shot version."""

from __future__ import annotations

from review_manager import ReviewRecorder
from project_manager import ProjectPaths
from project_state import CandidateStatus, ProjectCheckpoint, ShotStatus
from shot_storage import ShotStorageError, validate_bundle
from task_logger import TaskLogger


class ShotApprovalError(RuntimeError):
    """The current Shot state cannot be approved without changing semantics."""


def approve_shot_stage(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    recorder: ReviewRecorder | None = None,
    task_logger: TaskLogger | None = None,
) -> int:
    """Approve the active WAITING_REVIEW version without touching media snapshots."""

    entry = checkpoint.shot_checkpoint(shot_id)
    if checkpoint.shot_status(shot_id) is not ShotStatus.WAITING_REVIEW:
        raise ShotApprovalError("Only a WAITING_REVIEW Shot can be approved.")

    candidate = entry.get("candidate") or {}
    if str(candidate.get("status") or CandidateStatus.NONE.value) != CandidateStatus.NONE.value:
        raise ShotApprovalError("Candidate review belongs to a later workflow.")

    try:
        video_version = int(entry.get("active_video_version") or 0)
        prompt_version = int(entry.get("active_prompt_version") or 0)
    except (TypeError, ValueError) as error:
        raise ShotApprovalError("Shot approval pointers are invalid.") from error
    if video_version <= 0 or prompt_version <= 0:
        raise ShotApprovalError("Shot has no active review version.")
    if entry.get("approved_video_version") is not None:
        raise ShotApprovalError("Shot already has an approved video version.")

    generation = next(
        (
            item
            for item in entry.get("generation_versions") or []
            if isinstance(item, dict)
            if int(item.get("video_version") or 0) == video_version
        ),
        None,
    )
    if not isinstance(generation, dict):
        raise ShotApprovalError("Shot generation metadata is missing.")
    if int(generation.get("prompt_version") or 0) != prompt_version:
        raise ShotApprovalError("Shot Prompt and Video versions are not bound.")
    if str(generation.get("review_result") or generation.get("status")) != ShotStatus.WAITING_REVIEW.value:
        raise ShotApprovalError("Shot version is not waiting for review.")

    try:
        bundle = validate_bundle(paths, shot_id, video_version, require_video=True)
    except (OSError, ShotStorageError, ValueError) as error:
        raise ShotApprovalError("Shot review bundle is incomplete.") from error
    if str(bundle["review"].get("review_result")) != ShotStatus.WAITING_REVIEW.value:
        raise ShotApprovalError("Shot review record is not waiting for approval.")
    if int(bundle["prompt"].get("prompt_version") or 0) != prompt_version:
        raise ShotApprovalError("Bundle Prompt version does not match the active Prompt.")

    checkpoint.approve_shot(shot_id)
    if recorder is not None:
        recorder.record_shot_action(
            shot_id,
            "approve",
            prompt_version=prompt_version,
            video_version=video_version,
        )
    if task_logger is not None:
        task_logger.event(
            "SHOT_REVIEW_APPROVED",
            shot_id=shot_id,
            approved_prompt_version=prompt_version,
            approved_video_version=video_version,
            generation_count=entry.get("generation_count", 0),
        )
    return video_version
