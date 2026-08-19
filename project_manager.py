"""Create and expose every directory and business file path for one video project."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


OUTSIDE_PROJECT_MESSAGE = "目标文件路径不属于当前视频项目，已阻止写入。"

_WINDOWS_RETRYABLE_REPLACE_ERRORS = {5, 32, 33}
_ATOMIC_REPLACE_BACKOFF_SECONDS = (0.01, 0.02, 0.04, 0.08)


def _is_retryable_windows_replace_error(error: OSError) -> bool:
    """Return whether Windows reported a transient sharing/access conflict."""

    return getattr(error, "winerror", None) in _WINDOWS_RETRYABLE_REPLACE_ERRORS


def _replace_with_windows_retry(source: Path, target: Path) -> None:
    """Atomically replace once, retrying only transient Windows replace errors."""

    for attempt in range(len(_ATOMIC_REPLACE_BACKOFF_SECONDS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as error:
            if (
                not _is_retryable_windows_replace_error(error)
                or attempt >= len(_ATOMIC_REPLACE_BACKOFF_SECONDS)
            ):
                raise
            time.sleep(_ATOMIC_REPLACE_BACKOFF_SECONDS[attempt])


class ProjectDirectoryError(ValueError):
    """Raised when a project directory or managed target cannot be used safely."""


@dataclass(frozen=True)
class ProjectPaths:
    project_path: Path
    videos_dir: Path
    shots_dir: Path
    voice_dir: Path
    voice_scripts_dir: Path
    voice_versions_dir: Path
    subtitles_dir: Path
    subtitle_versions_dir: Path
    music_dir: Path
    music_assets_dir: Path
    music_versions_dir: Path
    exports_dir: Path
    export_versions_dir: Path
    references_dir: Path
    project_references_dir: Path
    visual_analysis_dir: Path
    evaluation_dir: Path
    evaluation_visual_analysis_dir: Path
    evaluation_prompts_dir: Path
    evaluation_generations_dir: Path
    evaluation_final_dir: Path
    concepts_dir: Path
    storyboard_dir: Path
    reviews_dir: Path
    logs_dir: Path
    task_logs_dir: Path
    llm_raw_logs_dir: Path
    error_logs_dir: Path
    api_logs_dir: Path

    def ensure_within_project(self, path: str | Path) -> Path:
        """Resolve and reject any target that escapes this video project."""
        project = self.project_path.resolve()
        target = Path(path).expanduser().resolve()
        if target != project and project not in target.parents:
            raise ProjectDirectoryError(OUTSIDE_PROJECT_MESSAGE)
        return target

    @staticmethod
    def _validate_shot_id(shot_id: int) -> int:
        if isinstance(shot_id, bool) or not isinstance(shot_id, int) or shot_id <= 0:
            raise ProjectDirectoryError("shot_id 必须是大于 0 的整数。")
        return shot_id

    def creative_brief_path(self) -> Path:
        return self.ensure_within_project(self.concepts_dir / "creative_brief.json")

    def project_state_path(self) -> Path:
        return self.ensure_within_project(self.project_path / "project.json")

    def voice_manifest_path(self) -> Path:
        return self.ensure_within_project(self.voice_dir / "voice_manifest.json")

    def voice_version_dir(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Voice version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.voice_versions_dir / f"v{version:03d}"
        )

    def voice_version_script_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.voice_version_dir(version) / "script.txt"
        )

    def voice_version_config_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.voice_version_dir(version) / "voice_config.json"
        )

    def voice_version_audio_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.voice_version_dir(version) / "audio.wav"
        )

    def voice_script_history_path(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Voice version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.voice_scripts_dir / f"script_v{version:03d}.txt"
        )

    def voice_staging_dir(self, version: int, operation_id: str) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Voice version 必须是大于 0 的整数。")
        safe_operation_id = self._safe_identifier(operation_id, fallback="voice")
        return self.ensure_within_project(
            self.voice_dir / f".staging_v{version:03d}_{safe_operation_id}"
        )

    def subtitle_manifest_path(self) -> Path:
        return self.ensure_within_project(
            self.subtitles_dir / "subtitle_manifest.json"
        )

    def subtitle_version_dir(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Subtitle version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.subtitle_versions_dir / f"v{version:03d}"
        )

    def subtitle_version_srt_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.subtitle_version_dir(version) / "subtitle.srt"
        )

    def subtitle_version_config_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.subtitle_version_dir(version) / "subtitle_config.json"
        )

    def subtitle_staging_dir(self, version: int, operation_id: str) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Subtitle version 必须是大于 0 的整数。")
        safe_operation_id = self._safe_identifier(operation_id, fallback="subtitle")
        return self.ensure_within_project(
            self.subtitles_dir
            / f".staging_v{version:03d}_{safe_operation_id}"
        )

    def music_manifest_path(self) -> Path:
        return self.ensure_within_project(self.music_dir / "music_manifest.json")

    def music_asset_path(self, sha256: str, extension: str) -> Path:
        safe_hash = str(sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", safe_hash):
            raise ProjectDirectoryError("Music SHA-256 无效。")
        safe_extension = self._validate_music_extension(extension)
        return self.ensure_within_project(
            self.music_assets_dir / f"music_{safe_hash[:16]}.{safe_extension}"
        )

    def music_version_dir(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Music version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.music_versions_dir / f"v{version:03d}"
        )

    def music_version_audio_path(self, version: int, extension: str) -> Path:
        safe_extension = self._validate_music_extension(extension)
        return self.ensure_within_project(
            self.music_version_dir(version) / f"music.{safe_extension}"
        )

    def music_version_config_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.music_version_dir(version) / "music_config.json"
        )

    def music_staging_dir(self, version: int, operation_id: str) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Music version 必须是大于 0 的整数。")
        safe_operation_id = self._safe_identifier(operation_id, fallback="music")
        return self.ensure_within_project(
            self.music_dir / f".staging_v{version:03d}_{safe_operation_id}"
        )

    @staticmethod
    def _validate_music_extension(extension: str) -> str:
        safe_extension = str(extension).strip().lower().lstrip(".")
        if safe_extension not in {"wav", "mp3", "flac", "ogg", "m4a", "aac"}:
            raise ProjectDirectoryError("Music 文件格式不受支持。")
        return safe_extension

    def export_manifest_path(self) -> Path:
        return self.ensure_within_project(self.exports_dir / "export_manifest.json")

    def export_version_dir(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Export version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.export_versions_dir / f"v{version:03d}"
        )

    def export_version_video_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.export_version_dir(version) / "final_video.mp4"
        )

    def export_version_voice_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.export_version_dir(version) / "voice.wav"
        )

    def export_version_metadata_path(self, version: int) -> Path:
        """Backward-compatible alias for the version-level export manifest."""
        return self.export_version_manifest_path(version)

    def export_version_manifest_path(self, version: int) -> Path:
        return self.ensure_within_project(
            self.export_version_dir(version) / "export_manifest.json"
        )

    def export_staging_dir(self, version: int, operation_id: str) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Export version 必须是大于 0 的整数。")
        safe_operation_id = self._safe_identifier(operation_id, fallback="export")
        return self.ensure_within_project(
            self.exports_dir / f".staging_v{version:03d}_{safe_operation_id}"
        )

    def storyboard_file_path(self) -> Path:
        return self.ensure_within_project(self.storyboard_dir / "storyboard.json")

    def video_prompts_path(self) -> Path:
        return self.ensure_within_project(
            self.storyboard_dir / "video_prompts.json"
        )

    def video_prompt_generation_progress_path(self) -> Path:
        """Persistent per-Shot VIDEO_PROMPT checkpoint (not a Shot Schema file)."""
        return self.ensure_within_project(
            self.storyboard_dir / "video_prompt_generation_progress.json"
        )

    def shot_dir(self, shot_id: int) -> Path:
        shot_id = self._validate_shot_id(shot_id)
        return self.ensure_within_project(self.shots_dir / f"shot_{shot_id:02d}")

    def shot_manifest_path(self, shot_id: int) -> Path:
        return self.ensure_within_project(self.shot_dir(shot_id) / "shot.json")

    def shot_editing_dir(self, shot_id: int) -> Path:
        return self.ensure_within_project(self.shot_dir(shot_id) / "editing")

    def shot_version_dir(self, shot_id: int, version: int) -> Path:
        shot_id = self._validate_shot_id(shot_id)
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Video version 必须是大于 0 的整数。")
        return self.ensure_within_project(
            self.shot_dir(shot_id) / f"v{version:03d}"
        )

    def shot_version_video_path(self, shot_id: int, version: int) -> Path:
        return self.ensure_within_project(
            self.shot_version_dir(shot_id, version) / "video.mp4"
        )

    def shot_version_prompt_path(self, shot_id: int, version: int) -> Path:
        return self.ensure_within_project(
            self.shot_version_dir(shot_id, version) / "prompt.json"
        )

    def shot_version_safety_path(self, shot_id: int, version: int) -> Path:
        return self.ensure_within_project(
            self.shot_version_dir(shot_id, version) / "safety.json"
        )

    def shot_version_generation_path(self, shot_id: int, version: int) -> Path:
        return self.ensure_within_project(
            self.shot_version_dir(shot_id, version) / "generation.json"
        )

    def shot_version_review_path(self, shot_id: int, version: int) -> Path:
        return self.ensure_within_project(
            self.shot_version_dir(shot_id, version) / "review.json"
        )

    def reference_manifest_path(self) -> Path:
        return self.ensure_within_project(
            self.references_dir / "reference_manifest.json"
        )

    def reference_asset_path(self, asset_id: str, extension: str) -> Path:
        safe_asset_id = self._safe_identifier(asset_id, fallback="reference")
        safe_extension = str(extension).lower().lstrip(".")
        if safe_extension not in {"jpg", "jpeg", "png", "webp"}:
            raise ProjectDirectoryError("Reference image extension is not supported.")
        return self.ensure_within_project(
            self.project_references_dir / f"{safe_asset_id}.{safe_extension}"
        )

    def visual_analysis_asset_dir(self, asset_id: str) -> Path:
        safe_asset_id = self._safe_identifier(asset_id, fallback="reference")
        return self.ensure_within_project(self.visual_analysis_dir / safe_asset_id)

    def visual_analysis_path(self, asset_id: str) -> Path:
        return self.ensure_within_project(
            self.visual_analysis_asset_dir(asset_id) / "analysis.json"
        )

    def visual_analysis_review_path(self) -> Path:
        return self.ensure_within_project(
            self.visual_analysis_dir / "visual_analysis_review.json"
        )

    def evaluation_visual_analysis_path(self) -> Path:
        return self.ensure_within_project(
            self.evaluation_visual_analysis_dir / "analysis.json"
        )

    def evaluation_prompt_path(self, stage: str) -> Path:
        filenames = {
            "creative": "creative_prompt.json",
            "storyboard": "storyboard_prompt.json",
            "video_prompt": "video_prompt.json",
        }
        safe_stage = str(stage).strip().lower()
        if safe_stage not in filenames:
            raise ProjectDirectoryError(f"不支持的 Evaluation Prompt 阶段：{stage}")
        return self.ensure_within_project(
            self.evaluation_prompts_dir / filenames[safe_stage]
        )

    def evaluation_generation_path(self, shot_id: int) -> Path:
        shot_id = self._validate_shot_id(shot_id)
        return self.ensure_within_project(
            self.evaluation_generations_dir
            / f"shot_{shot_id:02d}_generation.json"
        )

    def evaluation_final_video_path(self) -> Path:
        return self.ensure_within_project(
            self.evaluation_final_dir / "final_video.json"
        )

    @property
    def work_dir(self) -> Path:
        return self.ensure_within_project(self.project_path / "work")

    @property
    def assembly_work_dir(self) -> Path:
        return self.ensure_within_project(self.work_dir / "assembly")

    def shot_prompt_edit_path(self, shot_id: int, task_id: str) -> Path:
        shot_id = self._validate_shot_id(shot_id)
        safe_task_id = self._safe_identifier(task_id)
        return self.ensure_within_project(
            self.shot_editing_dir(shot_id) / f"prompt_edit_{safe_task_id}.txt"
        )

    def final_video_path(self) -> Path:
        return self.ensure_within_project(self.videos_dir / "final_video.mp4")

    def final_video_version_path(self, version: int) -> Path:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ProjectDirectoryError("Assembly version 必须是大于 0 的整数。")
        if version == 1:
            return self.final_video_path()
        return self.ensure_within_project(
            self.videos_dir / f"final_video_v{version:03d}.mp4"
        )

    def assembly_manifest_path(self) -> Path:
        return self.ensure_within_project(
            self.videos_dir / "assembly_manifest.json"
        )

    def assembly_run_dir(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.assembly_work_dir / self._safe_identifier(task_id)
        )

    def assembly_concat_list_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.assembly_run_dir(task_id) / "concat_list.txt"
        )

    def normalized_shot_path(self, task_id: str, shot_id: int) -> Path:
        shot_id = self._validate_shot_id(shot_id)
        return self.ensure_within_project(
            self.assembly_run_dir(task_id) / f"normalized_{shot_id:02d}.mp4"
        )

    def assembly_staged_output_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.assembly_run_dir(task_id) / "assembled_output.mp4"
        )

    def review_file_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.reviews_dir / f"review_{self._safe_identifier(task_id)}.json"
        )

    def task_log_file_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.task_logs_dir / f"task_{self._safe_identifier(task_id)}.log"
        )

    def error_log_file_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.error_logs_dir / f"error_{self._safe_identifier(task_id)}.log"
        )

    def api_log_file_path(self, task_id: str) -> Path:
        return self.ensure_within_project(
            self.api_logs_dir / f"api_{self._safe_identifier(task_id)}.log"
        )

    def llm_raw_file_path(self, stage: str, task_id: str, count: int = 1) -> Path:
        safe_stage = self._safe_identifier(stage, fallback="llm")
        safe_task_id = self._safe_identifier(task_id)
        suffix = "" if count == 1 else f"_{count:02d}"
        return self.ensure_within_project(
            self.llm_raw_logs_dir / f"{safe_stage}_{safe_task_id}{suffix}.txt"
        )

    @staticmethod
    def _safe_identifier(value: str, fallback: str = "task") -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
        return safe or fallback

    def save_json(self, path: str | Path, data: Any) -> Path:
        target = self.ensure_within_project(path)
        temporary_path = self.ensure_within_project(
            target.parent / f".{target.name}.{uuid4().hex}.tmp"
        )
        try:
            rendered = json.dumps(data, ensure_ascii=False, indent=2)
            with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_windows_retry(temporary_path, target)
        except OSError as exc:
            raise ProjectDirectoryError(f"项目文件保存失败：{target}：{exc}") from exc
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # The write/replace error remains authoritative. This path is
                # unique to this call, so cleanup never touches another writer.
                pass
        return target

    def managed_directories(self) -> tuple[Path, ...]:
        return (
            self.project_path,
            self.videos_dir,
            self.shots_dir,
            self.voice_dir,
            self.voice_scripts_dir,
            self.voice_versions_dir,
            self.subtitles_dir,
            self.subtitle_versions_dir,
            self.music_dir,
            self.music_assets_dir,
            self.music_versions_dir,
            self.exports_dir,
            self.export_versions_dir,
            self.references_dir,
            self.project_references_dir,
            self.visual_analysis_dir,
            self.evaluation_dir,
            self.evaluation_visual_analysis_dir,
            self.evaluation_prompts_dir,
            self.evaluation_generations_dir,
            self.evaluation_final_dir,
            self.concepts_dir,
            self.storyboard_dir,
            self.work_dir,
            self.assembly_work_dir,
            self.reviews_dir,
            self.logs_dir,
            self.task_logs_dir,
            self.llm_raw_logs_dir,
            self.error_logs_dir,
            self.api_logs_dir,
        )


def create_project_paths(
    project_path: str | Path,
    *,
    ensure_directories: bool = True,
) -> ProjectPaths:
    raw_path = str(project_path).strip().strip('"')
    if not raw_path:
        raise ProjectDirectoryError("项目保存目录不能为空。")

    selected_project_path = Path(raw_path).expanduser().resolve()
    logs_dir = selected_project_path / "logs"
    paths = ProjectPaths(
        project_path=selected_project_path,
        videos_dir=selected_project_path / "videos",
        shots_dir=selected_project_path / "shots",
        voice_dir=selected_project_path / "voice",
        voice_scripts_dir=selected_project_path / "voice" / "scripts",
        voice_versions_dir=selected_project_path / "voice" / "versions",
        subtitles_dir=selected_project_path / "subtitles",
        subtitle_versions_dir=selected_project_path / "subtitles" / "versions",
        music_dir=selected_project_path / "music",
        music_assets_dir=selected_project_path / "music" / "assets",
        music_versions_dir=selected_project_path / "music" / "versions",
        exports_dir=selected_project_path / "exports",
        export_versions_dir=selected_project_path / "exports",
        references_dir=selected_project_path / "references",
        project_references_dir=selected_project_path / "references" / "project",
        visual_analysis_dir=selected_project_path / "references" / "visual_analysis",
        evaluation_dir=selected_project_path / "evaluation",
        evaluation_visual_analysis_dir=selected_project_path
        / "evaluation"
        / "visual_analysis",
        evaluation_prompts_dir=selected_project_path / "evaluation" / "prompts",
        evaluation_generations_dir=selected_project_path
        / "evaluation"
        / "generations",
        evaluation_final_dir=selected_project_path / "evaluation" / "final",
        concepts_dir=selected_project_path / "concepts",
        storyboard_dir=selected_project_path / "storyboard",
        reviews_dir=selected_project_path / "reviews",
        logs_dir=logs_dir,
        task_logs_dir=logs_dir / "tasks",
        llm_raw_logs_dir=logs_dir / "llm_raw",
        error_logs_dir=logs_dir / "errors",
        api_logs_dir=logs_dir / "api",
    )
    if ensure_directories:
        try:
            for directory in paths.managed_directories():
                paths.ensure_within_project(directory)
                directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectDirectoryError(
                f"无法创建或使用项目目录 {selected_project_path}：{exc}"
            ) from exc
    return paths


def ask_project_paths() -> ProjectPaths:
    while True:
        raw_path = input("请选择本次视频项目保存目录：\n").strip()
        try:
            paths = create_project_paths(raw_path)
        except (ProjectDirectoryError, OSError) as exc:
            print(f"项目目录不可用：{exc}\n请重新输入。")
            continue
        print("\n项目目录：")
        print(paths.project_path)
        print("\n文件将保存至：")
        print(paths.project_path)
        return paths
