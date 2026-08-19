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
    candidate = entry.get("candidate") or {}
    if (
        checkpoint.shot_status(shot_id) is ShotStatus.APPROVED
        and str(candidate.get("status") or CandidateStatus.NONE.value)
        == CandidateStatus.WAITING_REVIEW.value
    ):
        try:
            video_version = int(candidate.get("video_version") or 0)
            prompt_version = int(candidate.get("prompt_version") or 0)
        except (TypeError, ValueError) as error:
            raise ShotApprovalError("Pending version pointers are invalid.") from error
        generation = next(
            (
                item
                for item in entry.get("generation_versions") or []
                if isinstance(item, dict)
                if int(item.get("video_version") or 0) == video_version
            ),
            None,
        )
        if (
            video_version <= 0
            or prompt_version <= 0
            or not isinstance(generation, dict)
            or int(generation.get("prompt_version") or 0) != prompt_version
            or str(generation.get("review_result") or generation.get("status"))
            != ShotStatus.WAITING_REVIEW.value
        ):
            raise ShotApprovalError("Pending version metadata is incomplete.")
        try:
            bundle = validate_bundle(paths, shot_id, video_version, require_video=True)
        except (OSError, ShotStorageError, ValueError) as error:
            raise ShotApprovalError("Pending version bundle is incomplete.") from error
        if (
            str(bundle["review"].get("review_result"))
            != ShotStatus.WAITING_REVIEW.value
            or int(bundle["prompt"].get("prompt_version") or 0) != prompt_version
        ):
            raise ShotApprovalError("Pending version binding is invalid.")
        old_prompt, old_video, new_prompt, new_video = checkpoint.approve_candidate(
            shot_id
        )
        assembly = checkpoint.data.get("assembly") or {}
        if (
            assembly.get("final_video_version") is not None
            or assembly.get("final_video_path")
            or str(assembly.get("status") or "").upper()
            in {"COMPLETED", "APPROVED", "STALE"}
        ):
            checkpoint.mark_assembly_needs_update(shot_id, old_video, new_video)
        if recorder is not None:
            recorder.record_shot_action(
                shot_id,
                "approve_pending_version",
                old_approved_prompt_version=old_prompt,
                old_approved_video_version=old_video,
                prompt_version=new_prompt,
                video_version=new_video,
            )
        if task_logger is not None:
            task_logger.event(
                "SHOT_PENDING_VERSION_APPROVED",
                shot_id=shot_id,
                old_approved_video_version=old_video,
                approved_prompt_version=new_prompt,
                approved_video_version=new_video,
                generation_count=entry.get("generation_count", 0),
            )
        return new_video

    if checkpoint.shot_status(shot_id) is not ShotStatus.WAITING_REVIEW:
        raise ShotApprovalError("Only a WAITING_REVIEW Shot can be approved.")

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
