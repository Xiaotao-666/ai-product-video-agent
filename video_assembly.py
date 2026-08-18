"""Validate and assemble approved Shot videos into one silent final video."""

from __future__ import annotations

import json
import inspect
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from project_manager import ProjectPaths
from project_state import (
    AssemblyStatus,
    ProjectCheckpoint,
    ShotStatus,
    now_iso,
)
from shot_storage import load_shot_manifest
from storyboard import Storyboard
from task_logger import TaskLogger


class AssemblyError(RuntimeError):
    """Raised when approved media cannot be safely assembled."""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MediaInfo:
    shot_id: int
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    fps_expression: str
    codec: str
    pixel_format: str
    has_audio: bool

    def manifest_dict(self, approved_video_version: int) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id,
            "approved_video_version": int(approved_video_version),
            "video_path": self.path.as_posix(),
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "codec": self.codec,
            "pixel_format": self.pixel_format,
            "has_audio": self.has_audio,
        }


def _run_command(
    command: list[str],
    *,
    runner: CommandRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def detect_ffmpeg_tools(
    *, runner: CommandRunner = subprocess.run
) -> dict[str, str]:
    """Return executable/version details or raise with the exact missing tool."""
    missing: list[str] = []
    details: dict[str, str] = {}
    for name in ("ffmpeg", "ffprobe"):
        executable = shutil.which(name)
        if not executable:
            missing.append(name)
            continue
        result = _run_command([executable, "-version"], runner=runner)
        if result.returncode != 0:
            missing.append(name)
            continue
        first_line = (result.stdout or result.stderr).splitlines()
        details[name] = first_line[0] if first_line else executable
    if missing:
        raise AssemblyError(
            "当前电脑未检测到 FFmpeg / FFprobe，无法生成完整视频。"
            f"\n缺少：{', '.join(missing)}。"
        )
    return details


def approved_shot_inputs(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
) -> list[dict[str, Any]]:
    """Select official active videos strictly in Storyboard order."""
    incomplete: list[tuple[int, str]] = []
    selected: list[dict[str, Any]] = []
    for shot in board.shots:
        status = checkpoint.shot_status(shot.shot_id)
        entry = checkpoint.shot_checkpoint(shot.shot_id)
        if status != ShotStatus.APPROVED:
            incomplete.append((shot.shot_id, status.value))
            continue
        version = entry.get("approved_video_version")
        if version is None:
            incomplete.append((shot.shot_id, "APPROVED_VERSION_MISSING"))
            continue
        manifest = load_shot_manifest(paths, shot.shot_id)
        if manifest.get("approved_version") != int(version):
            incomplete.append((shot.shot_id, "APPROVED_INDEX_MISMATCH"))
            continue
        selected.append(
            {
                "shot_id": shot.shot_id,
                "approved_video_version": int(version),
                "path": paths.shot_version_video_path(
                    shot.shot_id, int(version)
                ),
            }
        )
    if incomplete:
        lines = ["========== 暂时无法合片 ==========", "", "以下镜头尚未完成：", ""]
        lines.extend(f"Shot {shot_id:02d}：{status}" for shot_id, status in incomplete)
        lines.extend(["", "请先完成所有镜头审核。", "", "================================"])
        raise AssemblyError("\n".join(lines))
    return selected


def _parse_fps(raw: str | None) -> tuple[float, str]:
    expression = str(raw or "0/1")
    try:
        value = float(Fraction(expression))
    except (ValueError, ZeroDivisionError):
        value = 0.0
    return value, expression


def probe_media(
    ffprobe: str,
    shot_id: int,
    path: Path,
    paths: ProjectPaths,
    task_logger: TaskLogger,
    *,
    runner: CommandRunner = subprocess.run,
) -> MediaInfo:
    path = paths.ensure_within_project(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise AssemblyError(
            f"Shot {shot_id:02d} 无法正常读取。\n\n视频：\n{path}\n\n"
            "文件不存在或文件大小为 0，请重新生成或检查该 Shot。"
        )
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = _run_command(command, runner=runner)
    if result.returncode != 0:
        task_logger.event(
            "SHOT_MEDIA_VALIDATION",
            shot_id=shot_id,
            result="FAILED",
            video_path=path,
            return_code=result.returncode,
        )
        task_logger.error(
            f"ffprobe failed with return code {result.returncode}",
            stage="assembly_ffprobe",
            raw_response=result.stderr,
        )
        raise AssemblyError(
            f"Shot {shot_id:02d} 无法正常读取。\n\n视频：\n{path}\n\n"
            "ffprobe 无法解析该文件，请重新生成或检查该 Shot。"
        )
    try:
        payload = json.loads(result.stdout)
        streams = list(payload.get("streams") or [])
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
        duration = float((payload.get("format") or {}).get("duration"))
        width = int(video["width"])
        height = int(video["height"])
        fps, fps_expression = _parse_fps(
            video.get("avg_frame_rate") or video.get("r_frame_rate")
        )
        codec = str(video.get("codec_name") or "")
        pixel_format = str(video.get("pix_fmt") or "")
        if duration <= 0 or width <= 0 or height <= 0 or fps <= 0 or not codec:
            raise ValueError("媒体参数不完整")
    except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as exc:
        task_logger.error(exc, stage="assembly_ffprobe", raw_response=result.stdout)
        raise AssemblyError(
            f"Shot {shot_id:02d} 无法正常读取。\n\n视频：\n{path}\n\n"
            "ffprobe 返回的媒体参数不完整，请重新生成或检查该 Shot。"
        ) from exc
    info = MediaInfo(
        shot_id=shot_id,
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        fps_expression=fps_expression,
        codec=codec,
        pixel_format=pixel_format,
        has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
    )
    task_logger.event(
        "SHOT_MEDIA_VALIDATION",
        shot_id=shot_id,
        result="PASSED",
        video_path=path,
        duration=duration,
        resolution=f"{width}x{height}",
        fps=fps,
        codec=codec,
        pixel_format=pixel_format,
        has_audio=info.has_audio,
    )
    task_logger.event("FFPROBE_COMPLETED", shot_id=shot_id, video_path=path)
    return info


def media_are_concat_compatible(media: list[MediaInfo]) -> bool:
    if not media:
        return False
    first = media[0]
    return first.codec == "h264" and first.pixel_format == "yuv420p" and all(
        item.width == first.width
        and item.height == first.height
        and abs(item.fps - first.fps) < 0.001
        and item.codec == first.codec
        and item.pixel_format == first.pixel_format
        for item in media[1:]
    )


def _concat_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def _write_concat_list(paths: ProjectPaths, path: Path, media_paths: list[Path]) -> None:
    path = paths.ensure_within_project(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_concat_line(item) for item in media_paths), encoding="utf-8")


def _run_ffmpeg(
    command: list[str],
    task_logger: TaskLogger,
    stage: str,
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    result = _run_command(command, runner=runner)
    if result.returncode != 0:
        task_logger.error(
            f"FFmpeg return code {result.returncode}",
            stage=stage,
            raw_response=result.stderr,
        )
        raise AssemblyError(
            f"FFmpeg 执行失败（return code {result.returncode}）。\n{result.stderr.strip()}"
        )


def _concat_copy(
    ffmpeg: str,
    paths: ProjectPaths,
    task_id: str,
    media_paths: list[Path],
    output: Path,
    task_logger: TaskLogger,
    *,
    runner: CommandRunner,
) -> None:
    concat_list = paths.assembly_concat_list_path(task_id)
    _write_concat_list(paths, concat_list, media_paths)
    output.unlink(missing_ok=True)
    task_logger.event("VIDEO_CONCAT_STARTED", mode="stream_copy", output_path=output)
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            str(output),
        ],
        task_logger,
        "assembly_concat_copy",
        runner=runner,
    )


def _normalize_and_concat(
    ffmpeg: str,
    paths: ProjectPaths,
    task_id: str,
    media: list[MediaInfo],
    output: Path,
    task_logger: TaskLogger,
    *,
    runner: CommandRunner,
) -> None:
    first = media[0]
    target_width = first.width + (first.width % 2)
    target_height = first.height + (first.height % 2)
    target_fps = first.fps_expression
    normalized: list[Path] = []
    task_logger.event(
        "VIDEO_NORMALIZATION_STARTED",
        target_resolution=f"{target_width}x{target_height}",
        target_fps=target_fps,
        target_codec="h264",
        target_pixel_format="yuv420p",
    )
    for info in media:
        normalized_path = paths.normalized_shot_path(task_id, info.shot_id)
        normalized_path.unlink(missing_ok=True)
        video_filter = (
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={target_fps},setsar=1"
        )
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-i",
                str(info.path),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(normalized_path),
            ],
            task_logger,
            f"assembly_normalize_shot_{info.shot_id:02d}",
            runner=runner,
        )
        normalized.append(normalized_path)
        task_logger.event(
            "VIDEO_NORMALIZATION_COMPLETED",
            shot_id=info.shot_id,
            output_path=normalized_path,
        )
    _concat_copy(
        ffmpeg,
        paths,
        task_id,
        normalized,
        output,
        task_logger,
        runner=runner,
    )


def _load_manifest(paths: ProjectPaths) -> dict[str, Any]:
    path = paths.assembly_manifest_path()
    if not path.is_file():
        return {"manifest_version": 1, "assemblies": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"Assembly Manifest 无法读取：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assemblies"), list):
        raise AssemblyError("Assembly Manifest 结构无效。")
    return payload


def next_assembly_version(paths: ProjectPaths) -> int:
    versions: list[int] = []
    manifest = _load_manifest(paths)
    versions.extend(
        int(item.get("assembly_version") or 0)
        for item in manifest.get("assemblies", [])
    )
    if paths.final_video_path().is_file():
        versions.append(1)
    for path in paths.videos_dir.glob("final_video_v*.mp4"):
        suffix = path.stem.rsplit("v", 1)[-1]
        if suffix.isdigit():
            versions.append(int(suffix))
    return max(versions or [0]) + 1


def select_final_output(paths: ProjectPaths) -> tuple[Path, int] | None:
    manifest = _load_manifest(paths)
    existing = bool(manifest.get("assemblies")) or any(
        paths.videos_dir.glob("final_video*.mp4")
    )
    if not existing:
        return paths.final_video_path(), 1
    print("\n检测到已有完整视频。")
    print("\n请选择：")
    print("1. 保存为新版本（推荐，直接回车）")
    print("2. 覆盖当前版本")
    print("3. 取消")
    while True:
        choice = input("请输入 1、2 或 3 [1]: ").strip() or "1"
        if choice == "1":
            version = next_assembly_version(paths)
            return paths.final_video_version_path(version), version
        if choice == "2":
            assemblies = manifest.get("assemblies", [])
            if assemblies:
                latest = assemblies[-1]
                version = int(latest.get("assembly_version") or 1)
                recorded_path = latest.get("final_video_path")
                path = (
                    paths.ensure_within_project(paths.project_path / str(recorded_path))
                    if recorded_path
                    else paths.final_video_path()
                )
                return path, version
            return paths.final_video_path(), 1
        if choice == "3":
            return None
        print("无效选择，请输入 1、2 或 3。")


def _save_manifest(paths: ProjectPaths, record: dict[str, Any]) -> None:
    payload = _load_manifest(paths)
    assemblies = payload.setdefault("assemblies", [])
    assemblies = [
        item
        for item in assemblies
        if int(item.get("assembly_version") or 0) != int(record["assembly_version"])
    ]
    assemblies.append(record)
    assemblies.sort(key=lambda item: int(item.get("assembly_version") or 0))
    payload.update(
        {
            "manifest_version": 1,
            "assembly_version": int(record["assembly_version"]),
            "created_at": record["created_at"],
            "final_video_path": record["final_video_path"],
            "total_duration": record["total_duration"],
            "shots": record["shots"],
            "latest_assembly_version": int(record["assembly_version"]),
            "latest_final_video_path": record["final_video_path"],
            "assemblies": assemblies,
        }
    )
    paths.save_json(paths.assembly_manifest_path(), payload)


def assemble_approved_shots(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
    task_logger: TaskLogger,
    *,
    output_selection: tuple[Path, int] | None = None,
    runner: CommandRunner = subprocess.run,
) -> Path | None:
    selected = approved_shot_inputs(paths, checkpoint, board)
    task_logger.event("VIDEO_ASSEMBLY_READY", shot_count=len(selected))
    try:
        tools = detect_ffmpeg_tools(runner=runner)
    except AssemblyError as exc:
        checkpoint.fail_assembly(exc)
        task_logger.event("VIDEO_ASSEMBLY_FAILED", stage="tool_detection", error=exc)
        task_logger.error(exc, stage="assembly_tool_detection")
        raise
    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe_path = shutil.which("ffprobe") or "ffprobe"
    choice = output_selection if output_selection is not None else select_final_output(paths)
    if choice is None:
        return None
    final_path, assembly_version = choice
    final_path = paths.ensure_within_project(final_path)
    task_logger.event(
        "VIDEO_ASSEMBLY_CONFIRMED",
        assembly_version=assembly_version,
        final_video_path=final_path,
        ffmpeg=tools["ffmpeg"],
        ffprobe=tools["ffprobe"],
    )
    approved_snapshot = [
        {
            "shot_id": item["shot_id"],
            "approved_video_version": item["approved_video_version"],
            "video_path": item["path"].resolve().relative_to(
                paths.project_path.resolve()
            ).as_posix(),
        }
        for item in selected
    ]
    checkpoint.start_assembly(final_path, assembly_version, approved_snapshot)
    run_dir = paths.assembly_run_dir(task_logger.task_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    staged = paths.assembly_staged_output_path(task_logger.task_id)
    try:
        media = [
            probe_media(
                ffprobe_path,
                item["shot_id"],
                item["path"],
                paths,
                task_logger,
                runner=runner,
            )
            for item in selected
        ]
        print("\n========== Approved Shot 媒体检查 ==========")
        for info in media:
            print(
                f"Shot {info.shot_id:02d} | {info.width}x{info.height} | "
                f"{info.fps:.3f}fps | {info.codec} | {info.pixel_format} | "
                f"{info.duration:.2f}s | audio={'yes' if info.has_audio else 'no'}"
            )
        print("=" * 45)
        compatible = media_are_concat_compatible(media)
        assembly_mode = "concat_copy" if compatible else "normalized_concat"
        if compatible:
            try:
                _concat_copy(
                    ffmpeg_path,
                    paths,
                    task_logger.task_id,
                    [item.path for item in media],
                    staged,
                    task_logger,
                    runner=runner,
                )
            except AssemblyError as copy_error:
                assembly_mode = "normalized_concat"
                task_logger.event(
                    "VIDEO_CONCAT_COPY_FALLBACK",
                    reason=copy_error,
                )
                _normalize_and_concat(
                    ffmpeg_path,
                    paths,
                    task_logger.task_id,
                    media,
                    staged,
                    task_logger,
                    runner=runner,
                )
        else:
            _normalize_and_concat(
                ffmpeg_path,
                paths,
                task_logger.task_id,
                media,
                staged,
                task_logger,
                runner=runner,
            )
        final_info = probe_media(
            ffprobe_path,
            0,
            staged,
            paths,
            task_logger,
            runner=runner,
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        staged.replace(final_path)
        record = {
            "assembly_version": int(assembly_version),
            "created_at": now_iso(),
            "final_video_path": final_path.resolve().relative_to(
                paths.project_path.resolve()
            ).as_posix(),
            "total_duration": final_info.duration,
            "silent_video": True,
            "mode": assembly_mode,
            "shots": approved_snapshot,
        }
        _save_manifest(paths, record)
        checkpoint.complete_assembly(
            final_path,
            assembly_version,
            final_info.duration,
            approved_snapshot,
        )
        task_logger.event(
            "VIDEO_ASSEMBLY_COMPLETED",
            final_video_path=final_path,
            assembly_version=assembly_version,
            total_duration=final_info.duration,
            shot_count=len(selected),
        )
        shutil.rmtree(run_dir, ignore_errors=True)
        return final_path
    except (AssemblyError, OSError, ValueError) as exc:
        checkpoint.fail_assembly(exc)
        task_logger.event("VIDEO_ASSEMBLY_FAILED", error=exc)
        task_logger.error(exc, stage="video_assembly")
        raise


def display_participating_shots(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
) -> None:
    selected = approved_shot_inputs(paths, checkpoint, board)
    print("\n========== 参与合片的 Shot ==========")
    for item in selected:
        entry = checkpoint.shot_checkpoint(int(item["shot_id"]))
        print(
            f"Shot {item['shot_id']:02d} | Video "
            f"v{item['approved_video_version']} | Prompt "
            f"v{entry.get('approved_prompt_version')} | {item['path']}"
        )
    print("=" * 39)


def _open_specific_shot(
    open_shot_management: Callable[..., None], shot_id: int
) -> None:
    """Open a specific Shot when supported; keep legacy no-argument callbacks."""
    try:
        parameters = inspect.signature(open_shot_management).parameters.values()
        accepts_shot_id = any(
            parameter.kind
            in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL}
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_shot_id = False
    if accepts_shot_id:
        open_shot_management(int(shot_id))
    else:
        open_shot_management()


def confirm_assembly_versions(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
    open_shot_management: Callable[..., None],
) -> bool:
    """Confirm official Shot pointers before FFmpeg is allowed to run."""
    while True:
        selected = approved_shot_inputs(paths, checkpoint, board)
        print("\n========== 合片版本确认 ==========")
        print("\n即将参与合片：")
        for item in selected:
            entry = checkpoint.shot_checkpoint(int(item["shot_id"]))
            print(f"\nShot {int(item['shot_id']):02d}")
            print(f"Video：v{int(item['approved_video_version'])}")
            print(f"Prompt：v{entry.get('approved_prompt_version')}")
        print(f"\n共 {len(selected)} 个 Shot。")
        print("\n请选择：")
        print("1. 确认以上版本并合片")
        print("2. 修改 Shot 使用版本")
        print("3. 查看参与合片的视频")
        print("4. 暂不合片并返回")
        choice = input("请输入 1-4: ").strip()
        if choice == "1":
            return True
        if choice == "2":
            print("\n请选择 Shot：")
            for index, item in enumerate(selected, 1):
                print(
                    f"{index}. Shot {int(item['shot_id']):02d} → "
                    f"Video v{int(item['approved_video_version'])}"
                )
            print("0. 返回")
            raw = input("请输入编号：").strip()
            if raw == "0":
                continue
            if not raw.isdigit() or not 1 <= int(raw) <= len(selected):
                print("无效选择。")
                continue
            shot_id = int(selected[int(raw) - 1]["shot_id"])
            _open_specific_shot(open_shot_management, shot_id)
            continue
        if choice == "3":
            display_participating_shots(paths, checkpoint, board)
            continue
        if choice == "4":
            return False
        print("无效选择，请输入 1-4。")


def _assemble_after_version_confirmation(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
    task_logger: TaskLogger,
    open_shot_management: Callable[..., None],
    *,
    runner: CommandRunner,
) -> Path | None:
    if not confirm_assembly_versions(
        paths, checkpoint, board, open_shot_management
    ):
        return None
    task_logger.event("VIDEO_ASSEMBLY_CONFIRMED")
    return assemble_approved_shots(
        paths, checkpoint, board, task_logger, runner=runner
    )


def reconcile_running_assembly(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    task_logger: TaskLogger,
    *,
    runner: CommandRunner = subprocess.run,
) -> bool:
    assembly = checkpoint.assembly_checkpoint()
    if assembly.get("status") != AssemblyStatus.RUNNING.value:
        return False
    manifest = _load_manifest(paths)
    assemblies = manifest.get("assemblies", [])
    if not assemblies:
        return False
    latest = assemblies[-1]
    pending_version = assembly.get("pending_final_video_version")
    pending_path = assembly.get("pending_final_video_path")
    pending_shots = assembly.get("pending_shot_versions") or []
    if (
        pending_version is None
        or not pending_path
        or not pending_shots
        or int(latest.get("assembly_version") or 0) != int(pending_version)
        or str(latest.get("final_video_path") or "") != str(pending_path)
        or list(latest.get("shots") or []) != list(pending_shots)
    ):
        return False
    final_rel = latest.get("final_video_path")
    if not final_rel:
        return False
    final_path = paths.ensure_within_project(paths.project_path / str(final_rel))
    try:
        detect_ffmpeg_tools(runner=runner)
        ffprobe_path = shutil.which("ffprobe") or "ffprobe"
        info = probe_media(
            ffprobe_path, 0, final_path, paths, task_logger, runner=runner
        )
    except AssemblyError:
        return False
    shots = list(latest.get("shots") or [])
    if not shots:
        return False
    checkpoint.complete_assembly(
        final_path,
        int(latest.get("assembly_version") or 1),
        info.duration,
        shots,
    )
    return True


def _assembly_final_path(paths: ProjectPaths, checkpoint: ProjectCheckpoint) -> Path | None:
    relative = checkpoint.assembly_checkpoint().get("final_video_path")
    if not relative:
        return None
    return paths.ensure_within_project(paths.project_path / str(relative))


def _display_assembly_info(paths: ProjectPaths, checkpoint: ProjectCheckpoint) -> None:
    assembly = checkpoint.assembly_checkpoint()
    print("\n========== Assembly 信息 ==========")
    print(json.dumps(assembly, ensure_ascii=False, indent=2))
    manifest = paths.assembly_manifest_path()
    print(f"\nManifest：\n{manifest if manifest.is_file() else '尚未生成'}")
    print("=" * 37)


def _display_changes(checkpoint: ProjectCheckpoint, board: Storyboard) -> None:
    assembly = checkpoint.assembly_checkpoint()
    assembled = {
        int(item["shot_id"]): int(item["approved_video_version"])
        for item in assembly.get("shot_versions") or []
    }
    print("\n========== 合片后 Shot 变化 ==========")
    changed = False
    for shot in board.shots:
        latest = checkpoint.shot_checkpoint(shot.shot_id).get(
            "approved_video_version"
        )
        previous = assembled.get(shot.shot_id)
        if previous != latest:
            changed = True
            print(
                f"Shot {shot.shot_id:02d}：完整视频使用 v{previous}，"
                f"最新 Approved 为 v{latest}"
            )
    if not changed:
        print("Manifest 中未发现 Shot 版本差异。")
    print("=" * 41)


def assembly_menu(
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
    task_logger: TaskLogger,
    *,
    open_shot_management: Callable[[], None],
    runner: CommandRunner = subprocess.run,
) -> Path | None:
    """Human-confirmed assembly and Resume menu; never starts FFmpeg automatically."""
    try:
        selected = approved_shot_inputs(paths, checkpoint, board)
    except AssemblyError as exc:
        print(f"\n{exc}")
        task_logger.event("VIDEO_ASSEMBLY_READY", result="BLOCKED", error=exc)
        return None
    task_logger.event("VIDEO_ASSEMBLY_READY", shot_count=len(selected))

    while True:
        assembly = checkpoint.assembly_checkpoint()
        status = AssemblyStatus(str(assembly.get("status", AssemblyStatus.NOT_STARTED)))
        needs_update = bool(assembly.get("needs_update"))

        if status == AssemblyStatus.RUNNING:
            if reconcile_running_assembly(
                paths, checkpoint, task_logger, runner=runner
            ):
                print("\n检测到上次合片已经成功完成，状态已从 Manifest 恢复。")
                continue
            print("\n检测到上次合片异常中断，建议重新执行合片。")
            print("\n1. 重新执行合片")
            print("2. 返回")
            choice = input("请输入 1 或 2: ").strip()
            if choice == "1":
                try:
                    result = _assemble_after_version_confirmation(
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                        runner=runner,
                    )
                except AssemblyError as exc:
                    print(f"\n合片失败：{exc}")
                    continue
                if result:
                    return _assembly_success_menu(
                        result,
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                    )
                continue
            if choice == "2":
                return None
            print("无效选择。")
            continue

        if status == AssemblyStatus.COMPLETED and not needs_update:
            final_path = _assembly_final_path(paths, checkpoint)
            print("\n项目已经存在最新完整视频：")
            print(final_path or "project.json 未记录完整视频路径")
            print("\n请选择：")
            print("1. 查看完整视频")
            print("2. 重新生成完整视频")
            print("3. Shot 管理")
            print("4. 返回")
            choice = input("请输入 1-4: ").strip()
            if choice == "1":
                if final_path and final_path.is_file():
                    os_startfile(final_path)
                else:
                    print("记录的完整视频不存在，请选择重新生成。")
                continue
            if choice == "2":
                try:
                    result = _assemble_after_version_confirmation(
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                        runner=runner,
                    )
                except AssemblyError as exc:
                    print(f"\n合片失败：{exc}")
                    continue
                if result:
                    return _assembly_success_menu(
                        result,
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                    )
                continue
            if choice == "3":
                open_shot_management()
                continue
            if choice == "4":
                return final_path
            print("无效选择。")
            continue

        if status == AssemblyStatus.COMPLETED and needs_update:
            task_logger.event(
                "VIDEO_ASSEMBLY_OUTDATED",
                changed_shot_id=assembly.get("changed_shot_id"),
                old_approved_video_version=assembly.get(
                    "old_approved_video_version"
                ),
                new_approved_video_version=assembly.get(
                    "new_approved_video_version"
                ),
            )
            print("\n当前完整视频已过期。")
            print("有 Shot 在上次合片后发生更新。")
            final_export = (
                ((checkpoint.data.get("post_production") or {}).get("components") or {})
                .get("final_export")
                or {}
            )
            if final_export.get("status") == "COMPLETED":
                version = final_export.get("active_version")
                label = f" v{int(version):03d}" if version is not None else ""
                print(
                    f"已有 Final Export{label} 将继续保留，但需要在重新合片后更新。"
                )
            print("\n请选择：")
            print("1. 使用最新 Approved Shot 重新合片")
            print("2. 查看变化")
            print("3. Shot 管理")
            print("4. 暂不处理")
            choice = input("请输入 1-4: ").strip()
            if choice == "1":
                try:
                    result = _assemble_after_version_confirmation(
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                        runner=runner,
                    )
                except AssemblyError as exc:
                    print(f"\n合片失败：{exc}")
                    continue
                if result:
                    return _assembly_success_menu(
                        result,
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                    )
                continue
            if choice == "2":
                _display_changes(checkpoint, board)
                continue
            if choice == "3":
                open_shot_management()
                continue
            if choice == "4":
                return _assembly_final_path(paths, checkpoint)
            print("无效选择。")
            continue

        if status == AssemblyStatus.FAILED:
            print("\n上次完整视频合片失败。")
            print("\n1. 重新合片")
            print("2. 查看错误")
            print("3. 返回")
            choice = input("请输入 1-3: ").strip()
            if choice == "1":
                try:
                    result = _assemble_after_version_confirmation(
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                        runner=runner,
                    )
                except AssemblyError as exc:
                    print(f"\n合片失败：{exc}")
                    continue
                if result:
                    return _assembly_success_menu(
                        result,
                        paths,
                        checkpoint,
                        board,
                        task_logger,
                        open_shot_management,
                    )
                continue
            if choice == "2":
                print(json.dumps(assembly.get("last_error"), ensure_ascii=False, indent=2))
                continue
            if choice == "3":
                return None
            print("无效选择。")
            continue

        try:
            result = _assemble_after_version_confirmation(
                paths,
                checkpoint,
                board,
                task_logger,
                open_shot_management,
                runner=runner,
            )
        except AssemblyError as exc:
            print(f"\n合片失败：{exc}")
            continue
        if result:
            return _assembly_success_menu(
                result,
                paths,
                checkpoint,
                board,
                task_logger,
                open_shot_management,
            )
        return None


def os_startfile(path: Path) -> None:
    try:
        import os

        os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        raise AssemblyError(f"无法打开：{path}：{exc}") from exc


def _assembly_success_menu(
    final_path: Path,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    board: Storyboard,
    task_logger: TaskLogger,
    open_shot_management: Callable[[], None],
) -> Path:
    assembly = checkpoint.assembly_checkpoint()
    print("\n========== 完整视频生成成功 ==========")
    print(f"\n参与 Shot：\n{len(assembly.get('shot_versions') or board.shots)}")
    print(f"\n总时长：\n{float(assembly.get('total_duration') or 0):.2f} 秒")
    print(f"\n完整视频：\n\n{final_path}")
    print("\n====================================")
    while True:
        print("\n请选择：")
        print("1. 打开视频所在文件夹")
        print("2. 查看 Assembly 信息")
        print("3. 进入 Shot 管理")
        print("4. 结束本次任务")
        choice = input("请输入 1-4: ").strip()
        if choice == "1":
            os_startfile(paths.videos_dir)
            continue
        if choice == "2":
            _display_assembly_info(paths, checkpoint)
            continue
        if choice == "3":
            open_shot_management()
            continue
        if choice == "4":
            return final_path
        print("无效选择。")
