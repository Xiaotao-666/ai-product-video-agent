"""Explicit management workflow for reopening APPROVED Shots as Candidates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from project_manager import ProjectPaths
from project_state import (
    CandidateStatus,
    ProjectCheckpoint,
    ProjectStateError,
    ShotStatus,
    now_iso,
)
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import ReviewRecorder
from reference_assets import ReferenceAssetManager, select_candidate_visual_input
from shot_review import display_prompt_diff, open_prompt_editor
from shot_storage import update_generation_snapshot, write_review_snapshot
from storyboard import CreativeBrief, Storyboard, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_generation_request import VideoGenerationRequest
from video_model_selection import choose_and_confirm_video_generation
from video_provider import VideoProviderError
from video_provider_registry import VideoProviderRegistry, create_default_registry
from video_history import (
    VideoVersionInfo,
    create_historical_candidate,
    video_history_menu,
    video_version_history,
)


PromptReviser = Callable[[str, str], str]
PromptSafety = Callable[..., PromptSafetyReview]
VideoGenerator = Callable[..., Path]
PromptEditor = Callable[[Path], None]


class ShotManagerError(RuntimeError):
    """Raised when explicit Candidate management is inconsistent."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShotManagerError(f"无法读取版本文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ShotManagerError(f"版本文件必须是 JSON 对象：{path}")
    return value


def _write_prompt_payload(
    checkpoint: ProjectCheckpoint, payload: dict
) -> None:
    checkpoint.save_prompt_version(int(payload["shot_id"]), payload)


def _prompt_version_payload(
    checkpoint: ProjectCheckpoint, shot_id: int, version: int
) -> dict:
    payload = checkpoint.prompt_version(shot_id, version)
    if payload is None:
        raise ShotManagerError(f"Shot {shot_id:02d} Prompt v{version} 不存在。")
    return dict(payload)


def _approved_prompt(
    paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int
) -> dict:
    entry = checkpoint.shot_checkpoint(shot_id)
    version = entry.get("approved_prompt_version")
    if version is None:
        raise ShotManagerError(f"Shot {shot_id:02d} 没有 Approved Prompt version。")
    return _prompt_version_payload(checkpoint, shot_id, int(version))


def candidate_prompt(
    paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int
) -> dict:
    version = checkpoint.candidate_checkpoint(shot_id).get("prompt_version")
    if version is None:
        raise ShotManagerError(f"Shot {shot_id:02d} 新版本 Prompt 尚未确认。")
    return _prompt_version_payload(checkpoint, shot_id, int(version))


def _next_prompt_version(paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int) -> int:
    del paths
    entry = checkpoint.shot_checkpoint(shot_id)
    versions = [
        int(entry.get("prompt_version_count") or 0),
        *(int(item.get("version") or 0) for item in checkpoint.prompt_versions(shot_id)),
    ]
    return max(versions) + 1


def create_candidate_prompt_version(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    prompt: str,
    source: str,
    task_logger: TaskLogger,
    *,
    parent_version: int,
    original_prompt: str | None = None,
    user_feedback: str | None = None,
) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise ShotManagerError("新版本 Prompt 不能为空。")
    existing_candidate = checkpoint.candidate_checkpoint(shot_id)
    old_candidate_version = existing_candidate.get("prompt_version")
    if old_candidate_version is not None:
        old_payload = _prompt_version_payload(
            checkpoint, shot_id, int(old_candidate_version)
        )
        if old_payload.get("review_result") == "CANDIDATE":
            old_payload["review_result"] = "SUPERSEDED"
            old_payload["reviewed_at"] = now_iso()
            _write_prompt_payload(checkpoint, old_payload)
    version = _next_prompt_version(paths, checkpoint, shot_id)
    entry = checkpoint.shot_checkpoint(shot_id)
    payload = {
        "shot_id": int(shot_id),
        "version": version,
        "source": source,
        "candidate": True,
        "review_result": "CANDIDATE",
        "created_at": now_iso(),
        "prompt": prompt,
        "original_prompt": original_prompt,
        "edited_prompt": prompt if source == "manual_edit" else None,
        "parent_version": int(parent_version),
        "user_feedback": user_feedback,
        "safety_prompt": None,
        "safety_is_safe": None,
        "safety_risk_notes": [],
        "safety_checked_at": None,
        "base_approved_prompt_version": entry.get("approved_prompt_version"),
        "base_approved_video_version": entry.get("approved_video_version"),
    }
    _write_prompt_payload(checkpoint, payload)
    checkpoint.set_candidate_prompt(shot_id, version)
    task_logger.event(
        "CANDIDATE_CREATED",
        shot_id=shot_id,
        candidate_prompt_version=version,
        source=source,
        parent_version=parent_version,
        old_approved_prompt_version=entry.get("approved_prompt_version"),
        old_approved_video_version=entry.get("approved_video_version"),
    )
    return payload


def save_candidate_safety(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    safety: PromptSafetyReview,
) -> dict:
    payload = candidate_prompt(paths, checkpoint, shot_id)
    payload.update(
        {
            "safety_prompt": safety.reviewed_video_prompt,
            "safety_is_safe": safety.is_safe,
            "safety_risk_notes": list(safety.risk_notes),
            "safety_checked_at": now_iso(),
        }
    )
    _write_prompt_payload(checkpoint, payload)
    return payload


def _saved_candidate_safety(payload: dict) -> PromptSafetyReview | None:
    reviewed = payload.get("safety_prompt")
    if not isinstance(reviewed, str) or not reviewed.strip():
        return None
    return PromptSafetyReview(
        is_safe=bool(payload.get("safety_is_safe", True)),
        risk_notes=list(payload.get("safety_risk_notes") or []),
        reviewed_video_prompt=reviewed,
    )


def _editing_path(paths: ProjectPaths, shot_id: int, task_id: str) -> Path:
    return paths.shot_prompt_edit_path(shot_id, f"candidate_{task_id}")


def edit_candidate_prompt(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    task_logger: TaskLogger,
    original_prompt: str,
    *,
    editor: PromptEditor | None = None,
    resume_existing: bool = False,
    editing_path: Path | None = None,
) -> str | None:
    """Edit a project-local prefilled copy without activating it until confirmed."""
    active_editor = editor or open_prompt_editor
    previous_status = checkpoint.candidate_status(shot_id)
    previous_editing_path = checkpoint.candidate_checkpoint(shot_id).get("editing_path")
    previous_editing_original = checkpoint.candidate_checkpoint(shot_id).get(
        "editing_original_prompt"
    )
    path = paths.ensure_within_project(
        editing_path or _editing_path(paths, shot_id, task_logger.task_id)
    )
    if not resume_existing or not path.is_file():
        path.write_text(original_prompt, encoding="utf-8")
    checkpoint.begin_candidate_editing(shot_id, path)
    checkpoint.candidate_checkpoint(shot_id)["editing_original_prompt"] = original_prompt
    checkpoint.save()
    task_logger.event(
        "CANDIDATE_PROMPT_MANUAL_EDIT",
        shot_id=shot_id,
        action="started",
        editing_path=path,
    )
    while True:
        active_editor(path)
        try:
            edited = path.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise ShotManagerError(f"无法读取新版本临时编辑文件：{exc}") from exc
        display_prompt_diff(original_prompt, edited)
        print("\n请选择：")
        print("1. 确认修改并使用")
        print("2. 继续编辑")
        print("3. 放弃修改")
        print("4. 返回 Shot 管理")
        print("5. 取消本次新版本编辑")
        choice = input("请输入 1-5: ").strip()
        if choice == "1":
            if not edited:
                print("修改后的 Prompt 不能为空。")
                continue
            if edited == original_prompt:
                print("Prompt 没有变化，不会创建新的修改版本。")
                continue
            path.unlink(missing_ok=True)
            checkpoint.candidate_checkpoint(shot_id)["editing_path"] = None
            checkpoint.save()
            return edited
        if choice == "2":
            continue
        if choice in {"3", "4", "5"}:
            path.unlink(missing_ok=True)
            candidate = checkpoint.candidate_checkpoint(shot_id)
            candidate["status"] = previous_status.value
            candidate["editing_path"] = previous_editing_path
            candidate["editing_original_prompt"] = previous_editing_original
            candidate["updated_at"] = now_iso()
            checkpoint.save()
            return None
        print("无效选择，请输入 1-5。")


def _archive_candidate_attempt(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    task_logger: TaskLogger,
) -> Path | None:
    candidate = checkpoint.candidate_checkpoint(shot_id)
    version = candidate.get("video_version")
    if version is None:
        return None
    source = paths.shot_version_video_path(shot_id, int(version))
    if not source.is_file():
        return None
    # Bundle videos are immutable. Rejection is metadata-only.
    write_review_snapshot(
        paths,
        shot_id,
        int(version),
        review_result="REJECTED",
        user_action="candidate_rejected",
    )
    task_logger.event(
        "CANDIDATE_VIDEO_ARCHIVED",
        shot_id=shot_id,
        candidate_video_version=version,
        archived_path=source,
        storage_mode="immutable_bundle",
    )
    return source


def reject_candidate(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    shot_id: int,
) -> None:
    candidate = dict(checkpoint.candidate_checkpoint(shot_id))
    archived = _archive_candidate_attempt(paths, checkpoint, shot_id, task_logger)
    prompt_version = candidate.get("prompt_version")
    if prompt_version is not None and candidate.get("source") != "historical_video":
        payload = _prompt_version_payload(checkpoint, shot_id, int(prompt_version))
        payload["review_result"] = "REJECTED"
        payload["reviewed_at"] = now_iso()
        _write_prompt_payload(checkpoint, payload)
    checkpoint.finish_candidate(shot_id, "REJECTED", archived_video_path=archived)
    recorder.record_shot_action(
        shot_id,
        "candidate_rejected",
        old_approved_prompt_version=checkpoint.shot_checkpoint(shot_id).get(
            "approved_prompt_version"
        ),
        old_approved_video_version=checkpoint.shot_checkpoint(shot_id).get(
            "approved_video_version"
        ),
        candidate_prompt_version=prompt_version,
        candidate_video_version=candidate.get("video_version"),
    )
    task_logger.event(
        "CANDIDATE_REJECTED",
        shot_id=shot_id,
        candidate_prompt_version=prompt_version,
        candidate_video_version=candidate.get("video_version"),
    )


def generate_candidate_video(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    request: ProductVideoRequest,
    shot: StoryboardShot,
    shot_id: int,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    task_logger: TaskLogger,
    *,
    safety_review: PromptSafety,
    video_generate: VideoGenerator,
    recorder: ReviewRecorder | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
    reference_manager: ReferenceAssetManager | None = None,
) -> None:
    registry = provider_registry or create_default_registry(
        video_provider_credentials
    )
    candidate = checkpoint.candidate_checkpoint(shot_id)
    if checkpoint.candidate_status(shot_id) != CandidateStatus.GENERATING:
        checkpoint.prepare_candidate_generation(shot_id)
        candidate = checkpoint.candidate_checkpoint(shot_id)
    prompt_payload = candidate_prompt(paths, checkpoint, shot_id)
    resume_task = checkpoint.generation_provider_task(
        shot_id, candidate.get("video_version")
    )
    resuming = resume_task is not None
    safety = _saved_candidate_safety(prompt_payload)
    if safety is None:
        safety = safety_review(
            prompt_payload["prompt"],
            deepseek_key,
            task_logger,
            raw_stage=f"candidate_safety_shot_{shot_id:02d}",
        )
        save_candidate_safety(paths, checkpoint, shot_id, safety)
    if not safety.is_safe:
        error = ShotManagerError(f"Shot {shot_id:02d} 新版本 Prompt Safety 未通过。")
        checkpoint.mark_candidate_failed(shot_id, error)
        task_logger.event("CANDIDATE_FAILED", shot_id=shot_id, error=error)
        if recorder:
            _record_candidate_action(
                recorder, checkpoint, shot_id, "candidate_failed"
            )
        return
    version = int(candidate["video_version"])
    output = paths.shot_version_video_path(shot_id, version)
    generation_visual_input = (
        checkpoint.generation_visual_input(shot_id, version)
        if resuming
        else candidate.get("visual_input")
    )
    provider_selection = None
    if interactive_model_selection and not resuming:
        entry = checkpoint.shot_checkpoint(shot_id)
        previous_metadata = checkpoint.generation_provider_metadata(
            shot_id,
            int(entry.get("approved_video_version"))
            if entry.get("approved_video_version") is not None
            else None,
        ) or candidate.get("last_provider_route")
        while True:
            route_request = VideoGenerationRequest(
                shot_id=shot_id,
                prompt=safety.reviewed_video_prompt,
                duration=shot.duration,
                resolution="768P",
                visual_input=generation_visual_input,
                project=paths,
            )
            decision = choose_and_confirm_video_generation(
                registry,
                route_request,
                prompt_version=candidate.get("prompt_version"),
                regeneration=bool(entry.get("generation_count")),
                previous_metadata=previous_metadata,
            )
            if decision.action == "generate":
                provider_selection = decision.provider_selection
                task_logger.event(
                    "CANDIDATE_MODEL_SELECTION_CONFIRMED",
                    shot_id=shot_id,
                    candidate_video_version=candidate.get("video_version"),
                    **dict(decision.metadata or {}),
                )
                break
            if decision.action == "change_visual":
                if reference_manager is None:
                    checkpoint.defer_candidate_generation(shot_id)
                    task_logger.event(
                        "CANDIDATE_SUBMISSION_DEFERRED",
                        shot_id=shot_id,
                        reason="visual_input_manager_unavailable",
                    )
                    return
                visual = select_candidate_visual_input(
                    reference_manager,
                    shot_id,
                    generation_visual_input,
                )
                if visual is None:
                    checkpoint.defer_candidate_generation(shot_id)
                    task_logger.event(
                        "CANDIDATE_SUBMISSION_DEFERRED",
                        shot_id=shot_id,
                        reason="visual_input_selection_cancelled",
                    )
                    return
                generation_visual_input = reference_manager.validate_visual_input(
                    visual
                )
                checkpoint.set_candidate_visual_input(
                    shot_id, generation_visual_input
                )
                continue
            checkpoint.defer_candidate_generation(shot_id)
            task_logger.event(
                "CANDIDATE_SUBMISSION_CANCELLED", shot_id=shot_id
            )
            return
    task_logger.event(
        "CANDIDATE_VIDEO_GENERATING",
        shot_id=shot_id,
        candidate_prompt_version=candidate.get("prompt_version"),
        candidate_video_version=candidate.get("video_version"),
        resume_provider_task_id=(resume_task.provider_task_id if resume_task else None),
    )
    try:
        video_generate(
            provider_credentials=video_provider_credentials,
            prompt=safety.reviewed_video_prompt,
            duration=shot.duration,
            resolution="768P",
            project=paths,
            output_path=output,
            task_logger=task_logger,
            shot_id=shot_id,
            visual_input=generation_visual_input,
            provider_selection=provider_selection,
            provider_registry=registry,
            resume_task=resume_task,
            on_preflight=lambda metadata: checkpoint.mark_candidate_preflight(
                shot_id, metadata
            ),
            on_submitted=lambda task: checkpoint.mark_candidate_submitted(
                shot_id, task
            ),
            on_task_updated=lambda task: checkpoint.mark_candidate_task_updated(
                shot_id, task
            ),
        )
    except (VideoProviderError, OSError, RuntimeError) as exc:
        checkpoint.mark_candidate_failed(shot_id, exc)
        task_logger.event("CANDIDATE_FAILED", shot_id=shot_id, error=exc)
        task_logger.error(exc, stage=f"candidate_shot_{shot_id:02d}")
        if recorder:
            _record_candidate_action(
                recorder, checkpoint, shot_id, "candidate_failed"
            )
        return
    if not output.is_file() or output.stat().st_size <= 0:
        error = ShotManagerError("新版本 API 返回成功，但视频文件不存在或为空。")
        checkpoint.mark_candidate_failed(shot_id, error)
        task_logger.event("CANDIDATE_FAILED", shot_id=shot_id, error=error)
        if recorder:
            _record_candidate_action(
                recorder, checkpoint, shot_id, "candidate_failed"
            )
        return
    checkpoint.mark_candidate_ready(shot_id)
    task_logger.event(
        "CANDIDATE_VIDEO_READY",
        shot_id=shot_id,
        candidate_prompt_version=candidate.get("prompt_version"),
        candidate_video_version=candidate.get("video_version"),
        video_path=output,
    )
    if recorder:
        _record_candidate_action(
            recorder, checkpoint, shot_id, "candidate_video_ready"
        )


def approve_candidate(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    shot_id: int,
    *,
    selection_source: str = "new_version_review",
) -> None:
    entry = checkpoint.shot_checkpoint(shot_id)
    candidate = dict(checkpoint.candidate_checkpoint(shot_id))
    candidate_path = paths.shot_version_video_path(
        shot_id, int(candidate["video_version"])
    )
    if not candidate_path.is_file() or candidate_path.stat().st_size <= 0:
        raise ShotManagerError("待审核新版本视频不存在，不能设为正式版本。")
    old_prompt = entry.get("approved_prompt_version")
    old_video = entry.get("approved_video_version")
    new_prompt_payload = candidate_prompt(paths, checkpoint, shot_id)
    for item in plan.shots:
        if item.shot_id == shot_id:
            item.video_prompt = str(new_prompt_payload["prompt"])
            break
    paths.save_json(paths.video_prompts_path(), plan.model_dump())
    old_payload = _prompt_version_payload(checkpoint, shot_id, int(old_prompt))
    if int(old_prompt) != int(candidate["prompt_version"]):
        old_payload.setdefault("review_history", []).append(
            {
                "review_result": old_payload.get("review_result") or "APPROVED",
                "reviewed_at": old_payload.get("reviewed_at"),
                "superseded_at": now_iso(),
            }
        )
        old_payload["review_result"] = "SUPERSEDED_APPROVED"
        old_payload["reviewed_at"] = now_iso()
        _write_prompt_payload(checkpoint, old_payload)
    previous_new_prompt_result = new_prompt_payload.get("review_result")
    if previous_new_prompt_result and previous_new_prompt_result != "APPROVED":
        new_prompt_payload.setdefault("review_history", []).append(
            {
                "review_result": previous_new_prompt_result,
                "reviewed_at": new_prompt_payload.get("reviewed_at"),
                "reapproved_at": now_iso(),
                "selection_source": selection_source,
            }
        )
    new_prompt_payload["review_result"] = "APPROVED"
    new_prompt_payload["reviewed_at"] = now_iso()
    _write_prompt_payload(checkpoint, new_prompt_payload)
    review_action = (
        "historical_version_selected"
        if selection_source == "historical_version_selection"
        else "candidate_approved"
    )
    write_review_snapshot(
        paths,
        shot_id,
        int(candidate["video_version"]),
        review_result="APPROVED",
        user_action=review_action,
    )
    _, _, new_prompt, new_video = checkpoint.approve_candidate(shot_id)
    selected_at = now_iso()
    entry = checkpoint.shot_checkpoint(shot_id)
    for generation in entry.setdefault("generation_versions", []):
        if int(generation.get("video_version") or 0) == int(new_video):
            generation.update(
                {
                    "selected_as_approved_at": selected_at,
                    "previous_approved_version": old_video,
                    "selection_source": selection_source,
                }
            )
            break
    checkpoint.save()
    update_generation_snapshot(
        paths,
        shot_id,
        int(new_video),
        selected_as_approved_at=selected_at,
        previous_approved_version=old_video,
        selection_source=selection_source,
    )
    final_exists = any(paths.videos_dir.glob("*.mp4"))
    if final_exists:
        checkpoint.mark_assembly_needs_update(shot_id, old_video, new_video)
    recorder.record_shot_action(
        shot_id,
        "candidate_approved",
        old_approved_prompt_version=old_prompt,
        old_approved_video_version=old_video,
        candidate_prompt_version=new_prompt,
        candidate_video_version=new_video,
    )
    task_logger.event(
        "CANDIDATE_APPROVED",
        shot_id=shot_id,
        old_approved_prompt_version=old_prompt,
        old_approved_video_version=old_video,
        candidate_prompt_version=new_prompt,
        candidate_video_version=new_video,
    )
    if selection_source == "historical_version_selection":
        recorder.record_shot_action(
            shot_id,
            "historical_version_selected_as_approved",
            previous_approved_version=old_video,
            selected_video_version=new_video,
            selected_prompt_version=new_prompt,
            selection_source=selection_source,
            selected_at=selected_at,
        )
        task_logger.event(
            "SHOT_OFFICIAL_VERSION_SELECTED",
            shot_id=shot_id,
            previous_approved_version=old_video,
            selected_video_version=new_video,
            selected_prompt_version=new_prompt,
            selection_source=selection_source,
        )
    task_logger.event(
        "APPROVED_VERSION_REPLACED",
        shot_id=shot_id,
        old_approved_prompt_version=old_prompt,
        old_approved_video_version=old_video,
        new_approved_prompt_version=new_prompt,
        new_approved_video_version=new_video,
    )
    if final_exists:
        print(f"\nShot {shot_id:02d} 已更新。")
        print("当前完整视频仍使用旧版镜头，完整视频需要重新合片。")


def select_historical_version_as_approved(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    shot_id: int,
    target: VideoVersionInfo,
) -> None:
    """Use the existing Candidate transaction without exposing it in the UI."""
    create_historical_candidate(
        paths,
        checkpoint,
        shot_id,
        target.video_version,
        task_logger,
        recorder,
    )
    approve_candidate(
        paths,
        checkpoint,
        plan,
        recorder,
        task_logger,
        shot_id,
        selection_source="historical_version_selection",
    )


def show_history(paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int) -> None:
    del paths
    entry = checkpoint.shot_checkpoint(shot_id)
    generations = entry.setdefault("generation_versions", [])
    print(f"\n========== Shot {shot_id:02d} 历史 ==========")
    prompt_payloads = checkpoint.prompt_versions(shot_id)
    if not prompt_payloads:
        print("暂无 Prompt 历史。")
    for payload in prompt_payloads:
        version = int(payload.get("version", 0))
        related = [
            item for item in generations if item.get("prompt_version") == version
        ]
        video_versions = ", ".join(
            f"v{int(item['video_version'])}({item.get('status', 'HISTORY')})"
            for item in related
        ) or "无"
        result = payload.get("review_result")
        if not result and version == entry.get("approved_prompt_version"):
            result = "APPROVED"
        print(
            f"Prompt v{version} | source={payload.get('source', 'unknown')} | "
            f"video={video_versions} | created_at={payload.get('created_at')} | "
            f"result={result or 'HISTORY'}"
        )
    print("=" * 39)


def _record_candidate_action(
    recorder: ReviewRecorder,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    action: str,
) -> None:
    entry = checkpoint.shot_checkpoint(shot_id)
    candidate = checkpoint.candidate_checkpoint(shot_id)
    recorder.record_shot_action(
        shot_id,
        action,
        old_approved_prompt_version=entry.get("approved_prompt_version"),
        old_approved_video_version=entry.get("approved_video_version"),
        candidate_prompt_version=candidate.get("prompt_version"),
        candidate_video_version=candidate.get("video_version"),
    )


def _confirm_ai_candidate(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    approved: dict,
    candidate_text: str,
    feedback: str,
    revise: PromptReviser,
    task_logger: TaskLogger,
    editor: PromptEditor | None,
) -> bool:
    current = candidate_text
    combined_feedback = feedback
    while True:
        print("\n========== AI 修改后的新版本 Prompt ==========")
        print(current)
        print("=" * 38)
        print("\n请选择：")
        print("1. 确认并生成新视频版本")
        print("2. 继续让 AI 修改")
        print("3. 改为手动编辑")
        print("4. 放弃修改")
        print("5. 取消")
        choice = input("请输入 1-5: ").strip()
        if choice == "1":
            create_candidate_prompt_version(
                paths,
                checkpoint,
                shot_id,
                current,
                "ai_revision",
                task_logger,
                parent_version=int(approved["version"]),
                user_feedback=combined_feedback,
            )
            task_logger.event(
                "CANDIDATE_PROMPT_AI_REVISED",
                shot_id=shot_id,
                feedback=combined_feedback,
            )
            return True
        if choice == "2":
            more = input("请输入进一步修改意见：\n> ").strip()
            if not more:
                print("修改意见不能为空。")
                continue
            combined_feedback = f"{combined_feedback}\n{more}"
            current = revise(current, more)
            continue
        if choice == "3":
            edited = edit_candidate_prompt(
                paths,
                checkpoint,
                shot_id,
                task_logger,
                current,
                editor=editor,
            )
            if edited is None:
                continue
            create_candidate_prompt_version(
                paths,
                checkpoint,
                shot_id,
                edited,
                "manual_edit",
                task_logger,
                parent_version=int(approved["version"]),
                original_prompt=current,
            )
            return True
        if choice in {"4", "5"}:
            return False
        print("无效选择，请输入 1-5。")


def _create_candidate_from_approved(
    mode: str,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    task_logger: TaskLogger,
    revise: PromptReviser,
    editor: PromptEditor | None,
) -> bool:
    approved = _approved_prompt(paths, checkpoint, shot_id)
    checkpoint.begin_candidate_editing(shot_id, None)
    if mode == "same_prompt":
        checkpoint.set_candidate_prompt(shot_id, int(approved["version"]))
        task_logger.event(
            "CANDIDATE_CREATED",
            shot_id=shot_id,
            candidate_prompt_version=int(approved["version"]),
            source="same_prompt",
            parent_version=int(approved["version"]),
            old_approved_prompt_version=int(approved["version"]),
            old_approved_video_version=checkpoint.shot_checkpoint(shot_id).get(
                "approved_video_version"
            ),
        )
        return True
    if mode == "ai_revision":
        feedback = input("请输入修改意见：\n> ").strip()
        if not feedback:
            print("修改意见不能为空。")
            return False
        candidate_text = revise(str(approved["prompt"]), feedback)
        return _confirm_ai_candidate(
            paths,
            checkpoint,
            shot_id,
            approved,
            candidate_text,
            feedback,
            revise,
            task_logger,
            editor,
        )
    if mode == "manual_edit":
        edited = edit_candidate_prompt(
            paths,
            checkpoint,
            shot_id,
            task_logger,
            str(approved["prompt"]),
            editor=editor,
        )
        if edited is None:
            return False
        create_candidate_prompt_version(
            paths,
            checkpoint,
            shot_id,
            edited,
            "manual_edit",
            task_logger,
            parent_version=int(approved["version"]),
            original_prompt=str(approved["prompt"]),
        )
        return True
    raise ShotManagerError(f"未知新版本创建方式：{mode}")


def _version_info(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    video_version: int | None,
) -> VideoVersionInfo | None:
    if video_version is None:
        return None
    return next(
        (
            item
            for item in video_version_history(paths, checkpoint, shot_id)
            if item.video_version == int(video_version)
        ),
        None,
    )


def _print_version_summary(title: str, item: VideoVersionInfo | None) -> None:
    print(f"\n{title}：")
    if item is None:
        print("版本信息不可用。")
        return
    print(f"\nVideo：v{item.video_version}")
    print(f"Prompt：v{item.prompt_version if item.prompt_version is not None else '?'}")
    print(f"来源：{item.prompt_source}")
    print(f"模型：{item.provider_model}")
    print(f"生成时间：{item.created_at or '未知'}")
    print(
        "参考素材："
        + (", ".join(item.reference_asset_ids) if item.reference_asset_ids else "无")
    )


def _version_comparison_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
) -> None:
    entry = checkpoint.shot_checkpoint(shot_id)
    pending = checkpoint.candidate_checkpoint(shot_id)
    official = _version_info(
        paths, checkpoint, shot_id, entry.get("approved_video_version")
    )
    new_version = _version_info(
        paths, checkpoint, shot_id, pending.get("video_version")
    )
    while True:
        print(f"\n========== Shot {shot_id:02d} 版本对比 ==========")
        _print_version_summary("当前正式版本", official)
        _print_version_summary("新生成待审核版本", new_version)
        print("\n请选择：")
        print("1. 打开正式版本视频")
        print("2. 打开新版本视频")
        print("3. 查看正式版本 Prompt")
        print("4. 查看新版本 Prompt")
        print("5. 返回")
        choice = input("请输入 1-5: ").strip()
        if choice == "5":
            return
        if choice in {"1", "2"}:
            target = official if choice == "1" else new_version
            if target and target.video_path and target.video_path.is_file():
                os.startfile(target.video_path)  # type: ignore[attr-defined]
            else:
                print("对应视频文件不存在。")
            continue
        if choice in {"3", "4"}:
            target = official if choice == "3" else new_version
            payload = (
                checkpoint.prompt_version(shot_id, int(target.prompt_version))
                if target and target.prompt_version is not None
                else None
            )
            print("\n" + str((payload or {}).get("prompt") or "Prompt 内容不可用。"))
            continue
        print("无效选择，请输入 1-5。")


def _candidate_review_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    request: ProductVideoRequest,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    revise: PromptReviser,
    safety_review: PromptSafety,
    video_generate: VideoGenerator,
    editor: PromptEditor | None,
    provider_registry: VideoProviderRegistry | None,
    interactive_model_selection: bool,
    reference_manager: ReferenceAssetManager | None,
) -> None:
    shot_id = shot.shot_id
    while checkpoint.candidate_status(shot_id) == CandidateStatus.WAITING_REVIEW:
        entry = checkpoint.shot_checkpoint(shot_id)
        candidate = checkpoint.candidate_checkpoint(shot_id)
        print(f"\n========== Shot {shot_id:02d} 新版本生成完成 ==========")
        print(
            f"\n当前正式版本：\nVideo v{entry.get('approved_video_version')}\n"
            f"Prompt v{entry.get('approved_prompt_version')}"
        )
        print(
            f"\n新生成待审核版本：\nVideo v{candidate.get('video_version')}\n"
            f"Prompt v{candidate.get('prompt_version')}"
        )
        print(
            f"\n新版本视频：\n"
            f"{checkpoint.candidate_video_path(shot_id)}"
        )
        print("\n请选择：")
        print(f"1. 使用 Video v{candidate.get('video_version')} 作为新的正式版本")
        print(f"2. 使用 v{candidate.get('prompt_version')} Prompt 再生成一个新版本")
        print(f"3. AI 修改 v{candidate.get('prompt_version')} Prompt")
        print(f"4. 手动修改 v{candidate.get('prompt_version')} Prompt")
        print(f"5. 继续使用当前 Video v{entry.get('approved_video_version')}")
        print(
            f"6. 对比 v{entry.get('approved_video_version')} 与 "
            f"v{candidate.get('video_version')}"
        )
        print("7. 暂不处理并返回")
        choice = input("请输入 1-7: ").strip()
        if choice == "1":
            approve_candidate(paths, checkpoint, plan, recorder, task_logger, shot_id)
            return
        if choice == "2":
            _archive_candidate_attempt(paths, checkpoint, shot_id, task_logger)
            checkpoint.prepare_candidate_generation(shot_id)
            _record_candidate_action(recorder, checkpoint, shot_id, "candidate_retry")
            generate_candidate_video(
                paths,
                checkpoint,
                request,
                shot,
                shot_id,
                deepseek_key,
                video_provider_credentials,
                task_logger,
                safety_review=safety_review,
                video_generate=video_generate,
                recorder=recorder,
                provider_registry=provider_registry,
                interactive_model_selection=interactive_model_selection,
                reference_manager=reference_manager,
            )
            continue
        if choice in {"3", "4"}:
            current_payload = candidate_prompt(paths, checkpoint, shot_id)
            original_text = str(current_payload["prompt"])
            if choice == "3":
                feedback = input("请输入修改意见：\n> ").strip()
                if not feedback:
                    print("修改意见不能为空。")
                    continue
                revised = revise(original_text, feedback)
                confirmed = _confirm_ai_candidate(
                    paths,
                    checkpoint,
                    shot_id,
                    current_payload,
                    revised,
                    feedback,
                    revise,
                    task_logger,
                    editor,
                )
            else:
                edited = edit_candidate_prompt(
                    paths,
                    checkpoint,
                    shot_id,
                    task_logger,
                    original_text,
                    editor=editor,
                )
                confirmed = edited is not None
                if edited is not None:
                    create_candidate_prompt_version(
                        paths,
                        checkpoint,
                        shot_id,
                        edited,
                        "manual_edit",
                        task_logger,
                        parent_version=int(current_payload["version"]),
                        original_prompt=original_text,
                    )
            if confirmed:
                _record_candidate_action(
                    recorder,
                    checkpoint,
                    shot_id,
                    "candidate_prompt_ai_revised"
                    if choice == "3"
                    else "candidate_prompt_manual_edit",
                )
                _archive_candidate_attempt(paths, checkpoint, shot_id, task_logger)
                checkpoint.prepare_candidate_generation(shot_id)
                generate_candidate_video(
                    paths,
                    checkpoint,
                    request,
                    shot,
                    shot_id,
                    deepseek_key,
                    video_provider_credentials,
                    task_logger,
                    safety_review=safety_review,
                    video_generate=video_generate,
                    recorder=recorder,
                    provider_registry=provider_registry,
                    interactive_model_selection=interactive_model_selection,
                    reference_manager=reference_manager,
                )
            continue
        if choice == "5":
            reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
            return
        if choice == "6":
            _version_comparison_menu(paths, checkpoint, shot_id)
            continue
        if choice == "7":
            _record_candidate_action(
                recorder, checkpoint, shot_id, "candidate_review_deferred"
            )
            return
        print("无效选择，请输入 1-7。")


def _failed_candidate_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    request: ProductVideoRequest,
    shot: StoryboardShot,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    revise: PromptReviser,
    safety_review: PromptSafety,
    video_generate: VideoGenerator,
    editor: PromptEditor | None,
    provider_registry: VideoProviderRegistry | None,
    interactive_model_selection: bool,
    reference_manager: ReferenceAssetManager | None,
) -> None:
    shot_id = shot.shot_id
    while checkpoint.candidate_status(shot_id) == CandidateStatus.FAILED:
        print("\n新版本生成失败。")
        print("当前正式版本仍然安全保留。")
        print("\n请选择：")
        print("1. 重试生成新版本")
        print("2. 修改新版本 Prompt")
        print("3. 继续使用当前正式版本")
        print("4. 查看错误")
        print("5. 返回")
        choice = input("请输入 1-5: ").strip()
        if choice == "1":
            checkpoint.prepare_candidate_generation(shot_id)
            generate_candidate_video(
                paths, checkpoint, request, shot, shot_id, deepseek_key, video_provider_credentials,
                task_logger, safety_review=safety_review, video_generate=video_generate,
                recorder=recorder,
                provider_registry=provider_registry,
                interactive_model_selection=interactive_model_selection,
                reference_manager=reference_manager,
            )
            return
        if choice == "2":
            current = candidate_prompt(paths, checkpoint, shot_id)
            edited = edit_candidate_prompt(
                paths, checkpoint, shot_id, task_logger, str(current["prompt"]), editor=editor
            )
            if edited is not None:
                create_candidate_prompt_version(
                    paths, checkpoint, shot_id, edited, "manual_edit", task_logger,
                    parent_version=int(current["version"]), original_prompt=str(current["prompt"]),
                )
                checkpoint.prepare_candidate_generation(shot_id)
                generate_candidate_video(
                    paths, checkpoint, request, shot, shot_id, deepseek_key, video_provider_credentials,
                    task_logger, safety_review=safety_review, video_generate=video_generate,
                    recorder=recorder,
                    provider_registry=provider_registry,
                    interactive_model_selection=interactive_model_selection,
                    reference_manager=reference_manager,
                )
            return
        if choice == "3":
            reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
            return
        if choice == "4":
            print(json.dumps(checkpoint.candidate_checkpoint(shot_id).get("last_error"), ensure_ascii=False, indent=2))
            continue
        if choice == "5":
            return
        print("无效选择，请输入 1-5。")


def manage_approved_shot(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    request: ProductVideoRequest,
    plan: VideoPromptPlan,
    shot: StoryboardShot,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    *,
    revise: PromptReviser,
    safety_review: PromptSafety,
    video_generate: VideoGenerator,
    editor: PromptEditor | None = None,
    reference_manager: ReferenceAssetManager | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
) -> None:
    shot_id = shot.shot_id
    entry = checkpoint.shot_checkpoint(shot_id)
    task_logger.event(
        "APPROVED_SHOT_REOPENED",
        shot_id=shot_id,
        approved_prompt_version=entry.get("approved_prompt_version"),
        approved_video_version=entry.get("approved_video_version"),
    )
    while True:
        candidate_status = checkpoint.candidate_status(shot_id)
        if candidate_status == CandidateStatus.GENERATING:
            generate_candidate_video(
                paths, checkpoint, request, shot, shot_id, deepseek_key, video_provider_credentials,
                task_logger, safety_review=safety_review, video_generate=video_generate,
                recorder=recorder,
                provider_registry=provider_registry,
                interactive_model_selection=interactive_model_selection,
                reference_manager=reference_manager,
            )
            continue
        if candidate_status == CandidateStatus.WAITING_REVIEW:
            _candidate_review_menu(
                paths, checkpoint, request, plan, shot, recorder, task_logger,
                deepseek_key, video_provider_credentials, revise, safety_review, video_generate, editor,
                provider_registry, interactive_model_selection, reference_manager,
            )
            if checkpoint.candidate_status(shot_id) != CandidateStatus.WAITING_REVIEW:
                continue
            return
        if candidate_status == CandidateStatus.FAILED:
            _failed_candidate_menu(
                paths, checkpoint, request, shot, recorder, task_logger, deepseek_key,
                video_provider_credentials, revise, safety_review, video_generate, editor,
                provider_registry, interactive_model_selection, reference_manager,
            )
            if checkpoint.candidate_status(shot_id) != CandidateStatus.FAILED:
                continue
            return
        if candidate_status == CandidateStatus.EDITING:
            candidate = checkpoint.candidate_checkpoint(shot_id)
            editing_rel = candidate.get("editing_path")
            editing_path = (
                paths.ensure_within_project(paths.project_path / editing_rel)
                if editing_rel else None
            )
            if editing_path and editing_path.is_file():
                print("\n检测到上次存在未完成的新版本编辑。")
                print("1. 继续新版本编辑")
                print("2. 放弃本次编辑，继续使用当前正式版本")
                print("3. 返回")
                choice = input("请输入 1-3: ").strip()
                if choice == "1":
                    approved = _approved_prompt(paths, checkpoint, shot_id)
                    editing_original = str(
                        candidate.get("editing_original_prompt")
                        or approved["prompt"]
                    )
                    edited = edit_candidate_prompt(
                        paths, checkpoint, shot_id, task_logger,
                        editing_original, editor=editor, resume_existing=True,
                        editing_path=editing_path,
                    )
                    if edited is not None:
                        create_candidate_prompt_version(
                            paths, checkpoint, shot_id, edited, "manual_edit", task_logger,
                            parent_version=int(approved["version"]),
                            original_prompt=editing_original,
                        )
                        checkpoint.prepare_candidate_generation(shot_id)
                    continue
                if choice == "2":
                    editing_path.unlink(missing_ok=True)
                    reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
                    continue
                return
            if candidate.get("prompt_version") is not None:
                print("\n检测到已确认但尚未生成的新版本 Prompt。")
                print("1. 继续生成新版本")
                print("2. 放弃新版本，继续使用当前正式版本")
                print("3. 返回")
                choice = input("请输入 1-3: ").strip()
                if choice == "1":
                    checkpoint.prepare_candidate_generation(shot_id)
                    continue
                if choice == "2":
                    reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
                    continue
                return
            print("\n检测到未完成的新版本编辑，但没有可恢复的临时内容。")
            print("1. 放弃新版本，继续使用当前正式版本")
            print("2. 返回")
            if input("请输入 1 或 2: ").strip() == "1":
                reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
                continue
            return

        entry = checkpoint.shot_checkpoint(shot_id)
        print(f"\n========== Shot {shot_id:02d} ==========")
        print(f"\n状态：\n{entry['status']}")
        print(f"\nStoryboard：\n{shot.visual}\nCamera：\n{shot.camera}")
        official = next(
            (
                item
                for item in video_version_history(paths, checkpoint, shot_id)
                if item.video_version == entry.get("approved_video_version")
            ),
            None,
        )
        print("\n当前正式版本：")
        print(f"\nVideo：v{entry.get('approved_video_version')}")
        print(f"Prompt：v{entry.get('approved_prompt_version')}")
        print(f"模型：{official.provider_model if official else '未知'}")
        print(f"生成时间：{official.created_at if official else '未知'}")
        references = ", ".join(official.reference_asset_ids) if official else ""
        print(f"参考素材：{references or '无'}")
        print(f"\n当前视频：\n{checkpoint.approved_video_path(shot_id)}")
        print(f"\n累计生成：\n{entry.get('generation_count', 0)} 次")
        print("\n请选择：")
        print("1. 查看当前 Prompt")
        print("2. 打开当前视频")
        print("3. 使用当前 Prompt 再生成一个新版本")
        print("4. AI 修改 Prompt 并生成新版本")
        print("5. 手动修改 Prompt 并生成新版本")
        print("6. 查看 / 切换历史版本")
        print("7. 返回")
        choice = input("请输入 1-7: ").strip()
        if choice == "1":
            print(_approved_prompt(paths, checkpoint, shot_id)["prompt"])
            continue
        if choice == "2":
            video_path = checkpoint.approved_video_path(shot_id)
            if video_path.is_file():
                os.startfile(video_path)  # type: ignore[attr-defined]
            else:
                print("当前正式视频文件不存在。")
            continue
        if choice in {"3", "4", "5"}:
            mode = {"3": "same_prompt", "4": "ai_revision", "5": "manual_edit"}[choice]
            if _create_candidate_from_approved(
                mode, paths, checkpoint, shot_id, task_logger, revise, editor
            ):
                if reference_manager is not None:
                    visual = select_candidate_visual_input(
                        reference_manager,
                        shot_id,
                        checkpoint.approved_visual_input(shot_id),
                    )
                    if visual is None:
                        reject_candidate(
                            paths, checkpoint, recorder, task_logger, shot_id
                        )
                        continue
                    visual = reference_manager.validate_visual_input(visual)
                    checkpoint.set_candidate_visual_input(shot_id, visual)
                    task_logger.event(
                        "SHOT_VISUAL_INPUT_SELECTED",
                        shot_id=shot_id,
                        target="candidate",
                        visual_input_mode=visual["mode"],
                        reference_asset_ids=[
                            item["asset_id"] for item in visual["assets"]
                        ],
                    )
                _record_candidate_action(
                    recorder,
                    checkpoint,
                    shot_id,
                    {
                        "same_prompt": "candidate_created_same_prompt",
                        "ai_revision": "candidate_prompt_ai_revised",
                        "manual_edit": "candidate_prompt_manual_edit",
                    }[mode],
                )
                checkpoint.prepare_candidate_generation(shot_id)
            else:
                if checkpoint.candidate_status(shot_id) == CandidateStatus.EDITING:
                    reject_candidate(paths, checkpoint, recorder, task_logger, shot_id)
            continue
        if choice == "6":
            selected = video_history_menu(
                paths,
                checkpoint,
                shot_id,
                task_logger,
                recorder=recorder,
                approved_mode=True,
                approved_selector=lambda target: select_historical_version_as_approved(
                    paths,
                    checkpoint,
                    plan,
                    recorder,
                    task_logger,
                    shot_id,
                    target,
                ),
            )
            if selected:
                task_logger.event(
                    "APPROVED_SHOT_HISTORICAL_VERSION_SELECTED",
                    shot_id=shot_id,
                    approved_video_version=entry.get("approved_video_version"),
                    candidate_video_version=checkpoint.candidate_checkpoint(
                        shot_id
                    ).get("video_version"),
                )
            continue
        if choice == "7":
            return
        print("无效选择，请输入 1-7。")


def shot_management_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    plan: VideoPromptPlan,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    task_logger: TaskLogger,
    recorder: ReviewRecorder,
    *,
    revise_prompt: Callable[..., str],
    safety_review: PromptSafety,
    video_generate: VideoGenerator,
    editor: PromptEditor | None = None,
    reference_manager: ReferenceAssetManager | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
    initial_shot_id: int | None = None,
) -> None:
    shot_by_id = {shot.shot_id: shot for shot in board.shots}

    def manage(shot: StoryboardShot) -> None:
        if checkpoint.shot_status(shot.shot_id) != ShotStatus.APPROVED:
            print("当前 Shot 尚未正式通过，不能在正式版本管理中切换。")
            return
        revise = lambda current, feedback, selected=shot_by_id[shot.shot_id]: revise_prompt(
            request,
            brief,
            selected,
            current,
            feedback,
            deepseek_key,
            task_logger,
        )
        manage_approved_shot(
            paths,
            checkpoint,
            request,
            plan,
            shot,
            recorder,
            task_logger,
            deepseek_key,
            video_provider_credentials,
            revise=revise,
            safety_review=safety_review,
            video_generate=video_generate,
            editor=editor,
            reference_manager=reference_manager,
            provider_registry=provider_registry,
            interactive_model_selection=interactive_model_selection,
        )

    if initial_shot_id is not None:
        selected = shot_by_id.get(int(initial_shot_id))
        if selected is None:
            print(f"Shot {int(initial_shot_id):02d} 不存在。")
            return
        manage(selected)
        return

    while True:
        print("\n========== Shot 管理 ==========")
        for index, shot in enumerate(board.shots, 1):
            candidate = checkpoint.candidate_status(shot.shot_id)
            pending_labels = {
                CandidateStatus.EDITING: " / 新版本编辑中",
                CandidateStatus.GENERATING: " / 新版本生成中",
                CandidateStatus.WAITING_REVIEW: " / 待审核新版本",
                CandidateStatus.FAILED: " / 新版本生成失败",
            }
            suffix = pending_labels.get(candidate, "")
            entry = checkpoint.shot_checkpoint(shot.shot_id)
            versions = (
                f" — Video v{entry.get('approved_video_version')} / "
                f"Prompt v{entry.get('approved_prompt_version')}"
                if checkpoint.shot_status(shot.shot_id) == ShotStatus.APPROVED
                else ""
            )
            print(
                f"{index}. Shot {shot.shot_id:02d} "
                f"[{checkpoint.shot_status(shot.shot_id).value}]{versions}{suffix}"
            )
        print("0. 返回")
        raw = input("请选择要查看的 Shot：").strip()
        if raw == "0":
            return
        if not raw.isdigit() or not 1 <= int(raw) <= len(board.shots):
            print("无效选择。")
            continue
        shot = board.shots[int(raw) - 1]
        manage(shot)
