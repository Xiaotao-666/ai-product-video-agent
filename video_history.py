"""Project-scoped Shot video history inspection and local version switching."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from project_manager import ProjectPaths
from project_state import CandidateStatus, ProjectCheckpoint, ShotStatus, now_iso
from shot_storage import read_bundle_json
from review_manager import ReviewRecorder
from storyboard import VideoPromptPlan
from task_logger import TaskLogger
from video_provider_registry import infer_provider_from_model, normalize_provider_name
from visual_input import (
    normalize_visual_input,
    visual_input_asset_ids,
    visual_input_label,
)


class VideoHistoryError(RuntimeError):
    """Raised when a Shot history version cannot be inspected or restored safely."""


_VIDEO_VERSION_PATTERN = re.compile(r"^(?:v)?0*(\d+)$", re.IGNORECASE)


def parse_video_version(value: str) -> int:
    """Parse friendly version inputs such as ``1``, ``001`` or ``v001``."""
    match = _VIDEO_VERSION_PATTERN.fullmatch(str(value).strip())
    if match is None or int(match.group(1)) <= 0:
        raise VideoHistoryError(
            "视频版本格式无效，请输入 1、001、v1 或 v001 等正整数版本号。"
        )
    return int(match.group(1))


@dataclass(frozen=True)
class VideoVersionInfo:
    video_version: int
    prompt_version: int | None
    prompt_source: str
    created_at: str | None
    provider_task_id: str | None
    file_id: str | None
    video_path: Path | None
    review_result: str
    is_active: bool
    is_approved: bool
    exists: bool
    visual_input_mode: str
    visual_input_source: str | None
    reference_asset_ids: tuple[str, ...]
    generation_mode: str
    provider_model: str
    provider_api_version: str
    provider: str
    selection_mode: str
    credential_env_name: str | None


def _relative(paths: ProjectPaths, path: Path) -> str:
    return path.resolve().relative_to(paths.project_path.resolve()).as_posix()


def _project_path(paths: ProjectPaths, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value)
    return paths.ensure_within_project(
        raw if raw.is_absolute() else paths.project_path / raw
    )


def _prompt_source(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    version: int | None,
) -> str:
    if version is None:
        return "unknown"
    payload = checkpoint.prompt_version(shot_id, int(version))
    if payload is None:
        return "unknown"
    return str(payload.get("source") or "unknown")


def _resolve_video_path(
    paths: ProjectPaths,
    entry: dict[str, Any],
    generation: dict[str, Any],
    shot_id: int,
) -> Path | None:
    version = int(generation["video_version"])
    del entry, generation
    return paths.shot_version_video_path(shot_id, version)


def video_version_history(
    paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int
) -> list[VideoVersionInfo]:
    entry = checkpoint.shot_checkpoint(shot_id)
    changed = False
    versions: list[VideoVersionInfo] = []
    for generation in entry.setdefault("generation_versions", []):
        raw_version = generation.get("video_version")
        if raw_version is None:
            continue
        version = int(raw_version)
        prompt_version = generation.get("prompt_version")
        prompt_version = int(prompt_version) if prompt_version is not None else None
        source = _prompt_source(paths, checkpoint, shot_id, prompt_version)
        path = _resolve_video_path(paths, entry, generation, shot_id)
        is_active = version == entry.get("active_video_version")
        is_approved = version == entry.get("approved_video_version")
        created_at = generation.get("created_at") or generation.get("submitted_at")
        review_result = str(
            generation.get("review_result")
            or generation.get("status")
            or "HISTORY"
        )
        visual_input = normalize_visual_input(generation.get("visual_input"))
        bundle_generation: dict[str, Any] = {}
        try:
            bundle_generation = read_bundle_json(
                paths, shot_id, version, "generation.json"
            )
            visual_input = normalize_visual_input(
                bundle_generation.get("visual_input", visual_input)
            )
        except Exception:
            pass
        generation_mode = str(
            bundle_generation.get("generation_mode")
            or generation.get("generation_mode")
            or {
                "none": "text_to_video",
                "first_frame": "first_frame",
                "reference_asset": "reference_generation",
            }.get(visual_input["mode"], "unknown")
        )
        provider_model = str(
            bundle_generation.get("provider_model")
            or generation.get("provider_model")
            or "unknown"
        )
        provider_api_version = str(
            bundle_generation.get("provider_api_version")
            or generation.get("provider_api_version")
            or "unknown"
        )
        provider = normalize_provider_name(
            bundle_generation.get("provider") or generation.get("provider")
        ) or infer_provider_from_model(provider_model) or "unknown"
        selection_mode = str(
            bundle_generation.get("selection_mode")
            or generation.get("selection_mode")
            or "legacy"
        ).lower()
        credential_env_name = (
            bundle_generation.get("credential_env_name")
            or generation.get("credential_env_name")
        )
        metadata = {
            "prompt_source": source,
            "created_at": created_at,
            "video_path": _relative(paths, path) if path is not None else generation.get("video_path"),
            "review_result": review_result,
            "is_active": is_active,
            "is_approved": is_approved,
            "visual_input": visual_input,
            "generation_mode": generation_mode,
            "provider_model": provider_model,
            "provider_api_version": provider_api_version,
            "provider": provider,
            "selection_mode": selection_mode,
            "credential_env_name": credential_env_name,
        }
        for key, value in metadata.items():
            if generation.get(key) != value:
                generation[key] = value
                changed = True
        versions.append(
            VideoVersionInfo(
                video_version=version,
                prompt_version=prompt_version,
                prompt_source=source,
                created_at=created_at,
                provider_task_id=generation.get("provider_task_id"),
                file_id=generation.get("file_id"),
                video_path=path,
                review_result=review_result,
                is_active=is_active,
                is_approved=is_approved,
                exists=bool(path and path.is_file() and path.stat().st_size > 0),
                visual_input_mode=visual_input["mode"],
                visual_input_source=visual_input.get("source"),
                reference_asset_ids=tuple(visual_input_asset_ids(visual_input)),
                generation_mode=generation_mode,
                provider_model=provider_model,
                provider_api_version=provider_api_version,
                provider=provider,
                selection_mode=selection_mode,
                credential_env_name=(
                    str(credential_env_name) if credential_env_name else None
                ),
            )
        )
    if changed:
        entry["updated_at"] = now_iso()
        checkpoint.save()
    return sorted(versions, key=lambda item: item.video_version)


def display_video_history(
    paths: ProjectPaths, checkpoint: ProjectCheckpoint, shot_id: int
) -> list[VideoVersionInfo]:
    versions = video_version_history(paths, checkpoint, shot_id)
    candidate = checkpoint.candidate_checkpoint(shot_id)
    pending_version = (
        int(candidate["video_version"])
        if checkpoint.candidate_status(shot_id) != CandidateStatus.NONE
        and candidate.get("video_version") is not None
        else None
    )
    print(f"\n========== Shot {shot_id:02d} 视频版本 ==========")
    if not versions:
        print("\n暂无可识别的视频版本。")
    for item in versions:
        if item.is_approved:
            state_label = "当前正式"
        elif item.video_version == pending_version:
            state_label = "待审核新版本"
        else:
            state_label = "历史版本"
        missing = " / 文件缺失" if not item.exists else ""
        print(f"\nVideo v{item.video_version} [{state_label}{missing}]")
        print(
            f"Prompt：v{item.prompt_version if item.prompt_version is not None else '?'}"
        )
        print(f"状态：{state_label}")
        print(f"来源：{_prompt_source_label(item.prompt_source)}")
        print(f"生成时间：{item.created_at or '未知'}")
        print(f"任务 ID：{item.provider_task_id or '未知'}")
        print(f"历史审核结果：{item.review_result}")
        print(f"Visual Input：{visual_input_label(item.visual_input_mode)}")
        if item.reference_asset_ids:
            print(f"Reference：{', '.join(item.reference_asset_ids)}")
            print(f"Visual source：{item.visual_input_source or 'unknown'}")
        print(f"Model：{item.provider_model}")
        print(f"Provider：{item.provider}")
        print(f"API：{item.provider_api_version}")
        print(f"Generation mode：{item.generation_mode}")
        print(f"Model Selection：{item.selection_mode.upper()}")
        print(f"视频：\n{item.video_path or '文件路径未知'}")
    print(f"\n共记录：{len(versions)} 个视频版本")
    return versions


def _prompt_source_label(value: str) -> str:
    return {
        "ai_generated": "AI Generated",
        "ai_revision": "AI Revision",
        "manual_edit": "Manual Edit",
        "same_prompt": "Same Prompt",
    }.get(str(value or "unknown"), str(value or "unknown"))


def _version_by_number(
    versions: list[VideoVersionInfo], raw: str
) -> VideoVersionInfo:
    version = parse_video_version(raw)
    selected = next(
        (item for item in versions if item.video_version == version), None
    )
    if selected is None:
        raise VideoHistoryError(f"没有找到 Video v{version}。")
    return selected


def _display_switch_confirmation(
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    target: VideoVersionInfo,
) -> None:
    entry = checkpoint.shot_checkpoint(shot_id)
    print("\n========== 确认切换正式版本 ==========")
    print("\n当前正式版本：")
    print(f"\nVideo：v{entry.get('approved_video_version')}")
    print(f"Prompt：v{entry.get('approved_prompt_version')}")
    print("\n准备切换为：")
    print(f"\nVideo：v{target.video_version}")
    print(
        f"Prompt：v{target.prompt_version if target.prompt_version is not None else '?'}"
    )
    print("\n此次操作：")
    print("\n- 不重新调用视频 API")
    print("- 不重新生成视频")
    print("- 不删除任何历史版本")
    print(
        f"- Video v{entry.get('approved_video_version')} 将继续保留"
    )
    print(f"- 后续合片将使用 Video v{target.video_version}")
    print("- 已有完整合片和最终导出将标记为需要更新")


def switch_waiting_review_video(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    plan: VideoPromptPlan,
    shot_id: int,
    target_version: int,
    task_logger: TaskLogger,
    recorder: ReviewRecorder | None = None,
) -> VideoVersionInfo:
    if checkpoint.shot_status(shot_id) != ShotStatus.WAITING_REVIEW:
        raise VideoHistoryError("只有 WAITING_REVIEW Shot 可以直接切换历史版本。")
    versions = {item.video_version: item for item in video_version_history(paths, checkpoint, shot_id)}
    target = versions.get(int(target_version))
    if target is None:
        raise VideoHistoryError(f"Video v{target_version} 不存在于版本记录中。")
    if target.is_active:
        return target
    if not target.exists or target.video_path is None:
        raise VideoHistoryError(f"Video v{target_version} 文件缺失，不能恢复。")

    entry = checkpoint.shot_checkpoint(shot_id)
    current_version = int(entry.get("active_video_version") or 0)
    if current_version <= 0:
        raise VideoHistoryError("当前 active video version 无效，已阻止切换。")
    checkpoint.select_waiting_review_video_version(
        shot_id,
        target.video_version,
        target.prompt_version,
        current_version,
        target.video_path,
        provider_task_id=target.provider_task_id,
        file_id=target.file_id,
    )
    if target.prompt_version is not None:
        payload = checkpoint.prompt_version(shot_id, target.prompt_version)
        if payload is None:
            try:
                bundle_prompt = read_bundle_json(
                    paths, shot_id, target.video_version, "prompt.json"
                )
                payload = {"prompt": bundle_prompt.get("prompt_text", "")}
            except Exception:
                payload = None
        if payload:
            for item in plan.shots:
                if item.shot_id == shot_id:
                    item.video_prompt = str(payload["prompt"])
                    break
            paths.save_json(paths.video_prompts_path(), plan.model_dump())

    task_logger.event(
        "SHOT_VIDEO_HISTORY_SWITCHED",
        shot_id=shot_id,
        old_video_version=current_version,
        new_video_version=target.video_version,
        prompt_version=target.prompt_version,
        generation_count=entry.get("generation_count", 0),
    )
    if recorder:
        recorder.record_shot_action(
            shot_id,
            "switch_historical_video",
            old_video_version=current_version,
            new_video_version=target.video_version,
            prompt_version=target.prompt_version,
        )
    return target


def create_historical_candidate(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    target_version: int,
    task_logger: TaskLogger | None,
    recorder: ReviewRecorder | None = None,
    *,
    validated_target: VideoVersionInfo | None = None,
) -> VideoVersionInfo:
    if checkpoint.shot_status(shot_id) != ShotStatus.APPROVED:
        raise VideoHistoryError("只有已通过的 Shot 可以选择历史正式版本。")
    if checkpoint.candidate_status(shot_id) != CandidateStatus.NONE:
        raise VideoHistoryError("当前 Shot 已存在待处理的新版本，请先完成审核。")
    if validated_target is not None and validated_target.video_version != int(
        target_version
    ):
        raise VideoHistoryError("已验证的历史版本与目标版本不一致。")
    target = validated_target
    if target is None:
        versions = {
            item.video_version: item
            for item in video_version_history(paths, checkpoint, shot_id)
        }
        target = versions.get(int(target_version))
    if target is None:
        raise VideoHistoryError(f"Video v{target_version} 不存在于版本记录中。")
    if target.is_approved:
        raise VideoHistoryError("该版本已经是当前正式版本。")
    if target.prompt_version is None:
        raise VideoHistoryError("旧版本缺少 Prompt 对应信息，不能安全切换。")
    if not target.exists or target.video_path is None:
        raise VideoHistoryError(f"Video v{target_version} 文件缺失，不能恢复。")

    checkpoint.create_historical_video_candidate(
        shot_id,
        target.video_version,
        target.prompt_version,
        target.video_path,
        provider_task_id=target.provider_task_id,
        file_id=target.file_id,
    )
    if task_logger:
        task_logger.event(
            "HISTORICAL_VIDEO_CANDIDATE_CREATED",
            shot_id=shot_id,
            candidate_video_version=target.video_version,
            candidate_prompt_version=target.prompt_version,
            generation_count=checkpoint.shot_checkpoint(shot_id).get("generation_count", 0),
        )
    if recorder:
        recorder.record_shot_action(
            shot_id,
            "historical_video_candidate_created",
            candidate_video_version=target.video_version,
            candidate_prompt_version=target.prompt_version,
        )
    return target


def video_history_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    task_logger: TaskLogger,
    *,
    plan: VideoPromptPlan | None = None,
    recorder: ReviewRecorder | None = None,
    approved_mode: bool = False,
    approved_selector: Callable[[VideoVersionInfo], None] | None = None,
) -> bool:
    """Return True when a version was selected successfully."""
    while True:
        versions = display_video_history(paths, checkpoint, shot_id)
        task_logger.event("SHOT_VIDEO_HISTORY_VIEWED", shot_id=shot_id)
        print("\n请选择：")
        print(
            "1. 选择一个版本作为当前正式版本"
            if approved_mode
            else "1. 选择一个版本作为当前待审核版本"
        )
        print("2. 打开某个版本所在文件夹")
        print("3. 查看某个版本的 Prompt")
        print("4. 返回")
        choice = input("请输入 1-4: ").strip()
        if choice == "4":
            return False
        if choice == "2":
            raw = input("请输入要打开所在文件夹的 Video 版本号：").strip()
            try:
                selected = _version_by_number(versions, raw)
            except VideoHistoryError as exc:
                print(exc)
                continue
            if selected.video_path is None:
                print("该版本没有可识别的文件路径。")
                continue
            os.startfile(selected.video_path.parent)  # type: ignore[attr-defined]
            continue
        if choice == "3":
            raw = input("请输入要查看 Prompt 的 Video 版本号：").strip()
            try:
                selected = _version_by_number(versions, raw)
            except VideoHistoryError as exc:
                print(exc)
                continue
            if selected.prompt_version is None:
                print("该视频版本没有可识别的 Prompt 绑定。")
                continue
            payload = checkpoint.prompt_version(shot_id, selected.prompt_version)
            if payload is None:
                try:
                    payload = read_bundle_json(
                        paths, shot_id, selected.video_version, "prompt.json"
                    )
                except Exception:
                    payload = None
            prompt = (payload or {}).get("prompt") or (payload or {}).get(
                "prompt_text"
            )
            print(
                f"\n========== Video v{selected.video_version} / "
                f"Prompt v{selected.prompt_version} =========="
            )
            print(prompt or "Prompt 内容不可用。")
            print("=" * 48)
            continue
        if choice != "1":
            print("无效选择，请输入 1-4。")
            continue
        raw = input("请输入要使用的 Video 版本：").strip()
        try:
            selected = _version_by_number(versions, raw)
        except VideoHistoryError as exc:
            print(exc)
            continue
        if not selected.exists:
            print(f"Video v{selected.video_version} 文件缺失，不能选择。")
            continue
        if approved_mode and selected.is_approved:
            print(
                f"\nVideo v{selected.video_version} 已经是当前正式版本，无需切换。"
            )
            continue
        _display_switch_confirmation(checkpoint, shot_id, selected)
        confirm = input(
            f"\n1. 确认使用 Video v{selected.video_version}\n2. 取消\n"
            "请输入 1 或 2: "
        ).strip()
        if confirm != "1":
            print("已取消版本切换。")
            continue
        if "REJECTED" in selected.review_result.upper():
            print(
                f"\n⚠ Video v{selected.video_version} 曾经被标记为 REJECTED。\n"
                "如果继续，它将重新被人工批准为当前正式版本。"
            )
            rejected_confirm = input(
                f"\n1. 仍然使用 Video v{selected.video_version}\n2. 取消\n"
                "请输入 1 或 2: "
            ).strip()
            if rejected_confirm != "1":
                print("已取消版本切换。")
                continue
        try:
            if approved_mode:
                if approved_selector is not None:
                    approved_selector(selected)
                else:
                    create_historical_candidate(
                        paths,
                        checkpoint,
                        shot_id,
                        selected.video_version,
                        task_logger,
                        recorder,
                    )
                print(
                    f"\n切换成功：Video v{selected.video_version} "
                    "现在是当前正式版本。"
                )
            else:
                if plan is None:
                    raise VideoHistoryError("缺少 Video Prompt Plan，不能切换。")
                switch_waiting_review_video(
                    paths,
                    checkpoint,
                    plan,
                    shot_id,
                    selected.video_version,
                    task_logger,
                    recorder,
                )
                print(
                    f"\n已切换到 Video v{selected.video_version}，"
                    "未调用任何 API。"
                )
            return True
        except (OSError, ValueError, VideoHistoryError) as exc:
            print(f"切换失败：{exc}")
