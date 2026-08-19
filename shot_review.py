"""Per-Shot prompt/video versions and command-line human review."""

from __future__ import annotations

import json
import subprocess
import difflib
from pathlib import Path
from typing import Callable

from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, ShotStatus, now_iso
from prompt_generator import PromptSafetyReview, ProductVideoRequest
from review_manager import ReviewRecorder, TaskCancelled
from storyboard import CreativeBrief, ShotVideoPrompt, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_history import video_history_menu


class ShotReviewError(RuntimeError):
    """Raised when a Shot review or version operation is inconsistent."""


PromptReviser = Callable[[str, str], str]
PromptEditor = Callable[[Path], None]


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShotReviewError(f"无法读取 Prompt 版本文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ShotReviewError(f"Prompt 版本文件不是 JSON 对象：{path}")
    return value


def _prompt_item(plan: VideoPromptPlan, shot_id: int) -> ShotVideoPrompt:
    for item in plan.shots:
        if item.shot_id == shot_id:
            return item
    raise ShotReviewError(f"Video Prompt 缺少 Shot {shot_id:02d}。")


def _persist_prompt_plan(paths: ProjectPaths, plan: VideoPromptPlan) -> None:
    paths.save_json(paths.video_prompts_path(), plan.model_dump())


def create_prompt_version(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
    prompt: str,
    source: str,
    task_logger: TaskLogger | None,
    *,
    user_feedback: str | None = None,
    parent_version: int | None = None,
    original_prompt: str | None = None,
    persist_plan: bool = True,
) -> dict:
    if source not in {"ai_generated", "ai_revision", "manual_edit"}:
        raise ShotReviewError(f"未知 Prompt 来源：{source}")
    prompt = prompt.strip()
    if not prompt:
        raise ShotReviewError("Prompt 不能为空。")

    entry = checkpoint.shot_checkpoint(shot_id)
    parent = (
        int(parent_version)
        if parent_version is not None
        else (
            int(entry["active_prompt_version"])
            if entry.get("active_prompt_version") is not None
            else None
        )
    )
    existing_versions = [
        int(item.get("version") or 0)
        for item in checkpoint.prompt_versions(shot_id)
    ]
    version = max([int(entry.get("prompt_version_count", 0)), *existing_versions]) + 1
    payload = {
        "shot_id": shot_id,
        "version": version,
        "source": source,
        "created_at": now_iso(),
        "prompt": prompt,
        "original_prompt": original_prompt if source == "manual_edit" else None,
        "edited_prompt": prompt if source == "manual_edit" else None,
        "parent_version": parent,
        "user_feedback": user_feedback,
        "safety_prompt": None,
        "safety_checked_at": None,
    }
    if persist_plan:
        _prompt_item(plan, shot_id).video_prompt = prompt
        _persist_prompt_plan(paths, plan)
    checkpoint.save_prompt_version(shot_id, payload)
    if task_logger is not None:
        task_logger.event(
            "PROMPT_VERSION_CREATED",
            shot_id=shot_id,
            source=source,
            old_prompt_version=parent,
            new_prompt_version=version,
            video_version=entry.get("active_video_version"),
            generation_count=entry.get("generation_count", 0),
        )
    return payload


def ensure_initial_prompt_versions(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    task_logger: TaskLogger | None,
    *,
    persist_plan: bool = True,
) -> None:
    """Add v1 history to old/new prompt plans without changing active prompts."""
    for item in plan.shots:
        entry = checkpoint.shot_checkpoint(item.shot_id)
        active = entry.get("active_prompt_version")
        if active is not None and checkpoint.prompt_version(
            item.shot_id, int(active)
        ) is not None:
            continue
        create_prompt_version(
            paths,
            checkpoint,
            plan,
            item.shot_id,
            item.video_prompt,
            "ai_generated",
            task_logger,
            parent_version=None,
            persist_plan=persist_plan,
        )


def active_prompt_payload(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
) -> dict:
    entry = checkpoint.shot_checkpoint(shot_id)
    version = entry.get("active_prompt_version")
    if version is not None:
        payload = checkpoint.prompt_version(shot_id, int(version))
        if payload is not None:
            return dict(payload)
    return {
        "shot_id": shot_id,
        "version": version or 1,
        "source": "ai_generated",
        "created_at": None,
        "prompt": _prompt_item(plan, shot_id).video_prompt,
        "parent_version": None,
        "user_feedback": None,
        "safety_prompt": None,
        "safety_checked_at": None,
    }


def save_safety_to_active_prompt(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
    safety: PromptSafetyReview,
) -> None:
    entry = checkpoint.shot_checkpoint(shot_id)
    version = entry.get("active_prompt_version")
    if version is None:
        raise ShotReviewError(f"Shot {shot_id:02d} 尚未初始化 Prompt version。")
    payload = active_prompt_payload(paths, checkpoint, plan, shot_id)
    payload["safety_prompt"] = safety.reviewed_video_prompt
    payload["safety_is_safe"] = safety.is_safe
    payload["safety_risk_notes"] = safety.risk_notes
    payload["safety_checked_at"] = now_iso()
    checkpoint.save_prompt_version(shot_id, payload)


def active_prompt_safety(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
) -> PromptSafetyReview | None:
    payload = active_prompt_payload(paths, checkpoint, plan, shot_id)
    safety_prompt = payload.get("safety_prompt")
    if not isinstance(safety_prompt, str) or not safety_prompt.strip():
        return None
    return PromptSafetyReview(
        is_safe=bool(payload.get("safety_is_safe", True)),
        risk_notes=list(payload.get("safety_risk_notes") or []),
        reviewed_video_prompt=safety_prompt,
    )


def archive_active_video(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    task_logger: TaskLogger,
) -> Path | None:
    active = checkpoint.active_video_path(shot_id)
    if active is None:
        return None
    if not active.is_file() or active.stat().st_size <= 0:
        return None
    entry = checkpoint.shot_checkpoint(shot_id)
    version = int(
        entry.get("active_video_version")
        or entry.get("generation_count")
        or 1
    )
    # Schema v2 videos are immutable Bundle files. "Archiving" only clears
    # the active pointer when the next real generation becomes active.
    task_logger.event(
        "SHOT_VIDEO_VERSION_ARCHIVED",
        shot_id=shot_id,
        video_version=version,
        archived_path=active,
        storage_mode="immutable_bundle",
    )
    return active


def open_prompt_editor(path: Path) -> None:
    """Synchronously edit a prefilled UTF-8 Prompt copy with Windows Notepad."""
    try:
        subprocess.run(["notepad.exe", str(path)], check=True)
    except FileNotFoundError as exc:
        raise ShotReviewError("无法启动 Windows 记事本 notepad.exe。") from exc
    except subprocess.CalledProcessError as exc:
        raise ShotReviewError(f"记事本异常退出（代码 {exc.returncode}）。") from exc


def _write_edit_copy(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ShotReviewError(f"无法创建 Prompt 临时编辑副本：{exc}") from exc


def _read_edit_copy(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ShotReviewError(f"无法读取 Prompt 临时编辑副本：{exc}") from exc


def _cleanup_edit_copy(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ShotReviewError(f"无法清理 Prompt 临时编辑副本：{exc}") from exc


def display_prompt_diff(original: str, edited: str) -> None:
    print("\n========== Prompt 修改结果 ==========")
    print("\n修改前：")
    print(original)
    print("\n修改后：")
    print(edited)
    diff = list(
        difflib.unified_diff(
            original.splitlines(),
            edited.splitlines(),
            fromfile="修改前",
            tofile="修改后",
            lineterm="",
        )
    )
    print("\n差异：")
    print("\n".join(diff) if diff else "没有检测到变化。")
    print("=" * 37)


def manual_prompt_editor(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
    task_logger: TaskLogger,
    editor: PromptEditor | None = None,
    initial_prompt: str | None = None,
    cancel: Callable[[], None],
) -> bool:
    """Edit a temporary prefilled copy; activate only after explicit confirmation."""
    prompt_payload = active_prompt_payload(paths, checkpoint, plan, shot_id)
    original_prompt = str(prompt_payload["prompt"])
    candidate = initial_prompt if initial_prompt is not None else original_prompt
    active_editor = editor or open_prompt_editor
    path = paths.shot_prompt_edit_path(shot_id, task_logger.task_id)
    _write_edit_copy(path, candidate)
    task_logger.event(
        "SHOT_MANUAL_PROMPT_EDIT_STARTED",
        shot_id=shot_id,
        prompt_version=prompt_payload.get("version"),
        editing_path=path,
    )
    try:
        while True:
            active_editor(path)
            candidate = _read_edit_copy(path)
            display_prompt_diff(original_prompt, candidate)
            print("\n请选择：")
            print("1. 确认修改并使用")
            print("2. 继续编辑")
            print("3. 放弃修改，恢复原 Prompt")
            print("4. 返回 Shot 审核")
            print("5. 取消任务")
            choice = input("请输入 1-5: ").strip()
            if choice == "1":
                if not candidate:
                    print("修改后的 Prompt 不能为空，请继续编辑。")
                    continue
                if candidate == original_prompt:
                    print("Prompt 没有发生变化，不会创建新版本。")
                    continue
                parent = checkpoint.shot_checkpoint(shot_id).get(
                    "active_prompt_version"
                )
                create_prompt_version(
                    paths,
                    checkpoint,
                    plan,
                    shot_id,
                    candidate,
                    "manual_edit",
                    task_logger,
                    parent_version=parent,
                    original_prompt=original_prompt,
                )
                return True
            if choice == "2":
                # Keep the modified copy exactly as-is for the next editor round.
                continue
            if choice in {"3", "4"}:
                return False
            if choice == "5":
                cancel()
            print("无效选择，请输入 1-5。")
    finally:
        _cleanup_edit_copy(path)


def _confirm_revised_prompt(
    title: str,
    candidate: str,
    *,
    source: str,
    feedback: str | None,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
    task_logger: TaskLogger,
    revise_again: Callable[[str], str] | None,
    cancel: Callable[[], None],
    manual_editor: PromptEditor | None = None,
) -> bool:
    original = active_prompt_payload(paths, checkpoint, plan, shot_id)["prompt"]
    while True:
        heading = "AI 修改后的 Prompt" if source == "ai_revision" else "人工修改后的 Prompt"
        print(f"\n========== {heading} ==========")
        print(candidate)
        print("=" * (22 + len(heading)))
        print("\n请选择：")
        print("1. 确认使用，并重新生成当前 Shot")
        print("2. 继续输入修改意见")
        print("3. 改为人工手动编辑")
        print("4. 放弃本次修改，返回 Shot 审核")
        print("5. 取消任务")
        choice = input("请输入 1-5: ").strip()
        if choice == "1":
            parent = checkpoint.shot_checkpoint(shot_id).get("active_prompt_version")
            create_prompt_version(
                paths,
                checkpoint,
                plan,
                shot_id,
                candidate,
                source,
                task_logger,
                user_feedback=feedback,
                parent_version=parent,
            )
            return True
        if choice == "2":
            more = input("请输入进一步修改意见: ").strip()
            if not more:
                print("修改意见不能为空。")
                continue
            if revise_again is None:
                raise ShotReviewError("AI Prompt 修改函数未提供。")
            feedback = f"{feedback}\n{more}" if feedback else more
            candidate = revise_again(feedback)
            continue
        if choice == "3":
            return manual_prompt_editor(
                paths=paths,
                checkpoint=checkpoint,
                plan=plan,
                shot_id=shot_id,
                task_logger=task_logger,
                editor=manual_editor,
                initial_prompt=candidate,
                cancel=cancel,
            )
        if choice == "4":
            return False
        if choice == "5":
            cancel()
        print("无效选择，请输入 1-5。")


def shot_video_review_gate(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    request: ProductVideoRequest,
    brief: CreativeBrief,
    shot: StoryboardShot,
    recorder: ReviewRecorder,
    task_logger: TaskLogger,
    ai_revise: PromptReviser,
    manual_editor: PromptEditor | None = None,
) -> str:
    """Return approve or regenerate; cancellation raises TaskCancelled."""
    del request, brief  # Context is already captured by ai_revise.
    shot_id = shot.shot_id
    stage_name = f"shot_{shot_id:02d}_video_review"
    entry = checkpoint.shot_checkpoint(shot_id)

    def cancel() -> None:
        checkpoint.cancel(ProjectStage.VIDEO_GENERATION, shot_id=shot_id)
        recorder.cancel_shot(shot_id, f"Shot {shot_id:02d}审核")
        raise TaskCancelled(f"Shot {shot_id:02d}审核")

    task_logger.set_stage(stage_name)
    task_logger.event(
        "SHOT_VIDEO_READY_FOR_REVIEW",
        shot_id=shot_id,
        video_path=checkpoint.active_video_path(shot_id),
        prompt_version=entry.get("active_prompt_version"),
        video_version=entry.get("active_video_version"),
        generation_count=entry.get("generation_count", 0),
    )
    recorder.record_shot_action(
        shot_id,
        "waiting_review",
        prompt_version=entry.get("active_prompt_version"),
        video_version=entry.get("active_video_version"),
    )

    while True:
        entry = checkpoint.shot_checkpoint(shot_id)
        prompt = active_prompt_payload(paths, checkpoint, plan, shot_id)
        print(f"\n========== Shot {shot_id:02d} 已生成 ==========")
        print(f"\nStoryboard：\n{shot.visual}")
        print(f"\n当前视频：\n{checkpoint.active_video_path(shot_id)}")
        print(f"\n当前 Prompt 版本：\nv{int(prompt['version'])}")
        print(f"\n当前视频版本：\nv{int(entry.get('active_video_version') or 0)}")
        print(f"\n本镜头已生成次数：\n{entry.get('generation_count', 0)}")
        print("\n请先打开视频观看。")
        print("\n请选择：")
        print("1. 通过当前 Shot，继续下一镜头")
        print("2. 使用当前 Prompt 重新生成当前 Shot")
        print("3. AI 辅助修改 Prompt 后重新生成")
        print("4. 查看 / 手动编辑当前 Prompt")
        print("5. 查看 / 切换历史视频版本")
        print("6. 取消本次任务")
        choice = input("请输入 1-6: ").strip()
        if choice == "1":
            checkpoint.approve_shot(shot_id)
            recorder.record_shot_action(
                shot_id,
                "approve",
                prompt_version=entry.get("active_prompt_version"),
                video_version=entry.get("active_video_version"),
            )
            task_logger.event(
                "SHOT_REVIEW_APPROVED",
                shot_id=shot_id,
                approved_prompt_version=entry.get("active_prompt_version"),
                approved_video_version=entry.get("active_video_version"),
                generation_count=entry.get("generation_count", 0),
            )
            return "approve"
        if choice == "2":
            recorder.record_shot_action(shot_id, "regenerate_same_prompt")
            task_logger.event(
                "SHOT_REVIEW_REJECTED", shot_id=shot_id, action="same_prompt"
            )
            task_logger.event(
                "SHOT_REGENERATE_SAME_PROMPT",
                shot_id=shot_id,
                prompt_version=entry.get("active_prompt_version"),
                video_version=entry.get("active_video_version"),
                generation_count=entry.get("generation_count", 0),
            )
            return "regenerate"
        if choice == "3":
            feedback = input("请输入你希望如何修改当前镜头：\n> ").strip()
            if not feedback:
                print("修改意见不能为空。")
                continue
            task_logger.event(
                "SHOT_REVIEW_REJECTED", shot_id=shot_id, action="ai_prompt_revision"
            )
            candidate = ai_revise(prompt["prompt"], feedback)
            task_logger.event(
                "SHOT_AI_PROMPT_REVISION",
                shot_id=shot_id,
                old_prompt_version=entry.get("active_prompt_version"),
                feedback=feedback,
            )
            confirmed = _confirm_revised_prompt(
                "AI 修改后的 Prompt",
                candidate,
                source="ai_revision",
                feedback=feedback,
                paths=paths,
                checkpoint=checkpoint,
                plan=plan,
                shot_id=shot_id,
                task_logger=task_logger,
                revise_again=lambda combined: ai_revise(prompt["prompt"], combined),
                cancel=cancel,
                manual_editor=manual_editor,
            )
            if confirmed:
                new_entry = checkpoint.shot_checkpoint(shot_id)
                recorder.record_shot_action(shot_id, "ai_prompt_revision", feedback)
                task_logger.event(
                    "SHOT_AI_PROMPT_REVISION",
                    shot_id=shot_id,
                    action="confirmed",
                    old_prompt_version=entry.get("active_prompt_version"),
                    new_prompt_version=new_entry.get("active_prompt_version"),
                    video_version=new_entry.get("active_video_version"),
                    generation_count=new_entry.get("generation_count", 0),
                )
                return "regenerate"
            continue
        if choice == "4":
            print("\n========== 当前 Shot Prompt ==========")
            print(prompt["prompt"])
            print("=" * 39)
            print("\n请选择：")
            print("1. 返回")
            print("2. 在当前 Prompt 基础上编辑")
            edit_choice = input("请输入 1 或 2: ").strip()
            if edit_choice == "1":
                continue
            if edit_choice != "2":
                print("无效选择。")
                continue
            task_logger.event(
                "SHOT_REVIEW_REJECTED", shot_id=shot_id, action="manual_prompt_edit"
            )
            confirmed = manual_prompt_editor(
                paths=paths,
                checkpoint=checkpoint,
                plan=plan,
                shot_id=shot_id,
                task_logger=task_logger,
                editor=manual_editor,
                cancel=cancel,
            )
            if confirmed:
                new_entry = checkpoint.shot_checkpoint(shot_id)
                recorder.record_shot_action(shot_id, "manual_prompt_edit")
                task_logger.event(
                    "SHOT_MANUAL_PROMPT_EDIT",
                    shot_id=shot_id,
                    old_prompt_version=entry.get("active_prompt_version"),
                    new_prompt_version=new_entry.get("active_prompt_version"),
                    video_version=new_entry.get("active_video_version"),
                    generation_count=new_entry.get("generation_count", 0),
                )
                return "regenerate"
            continue
        if choice == "5":
            video_history_menu(
                paths,
                checkpoint,
                shot_id,
                task_logger,
                plan=plan,
                recorder=recorder,
            )
            continue
        if choice == "6":
            cancel()
        print("无效选择，请输入 1-6。")
