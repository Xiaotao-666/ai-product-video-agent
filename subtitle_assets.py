"""Immutable project-local storage for generated subtitle versions."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from project_manager import ProjectDirectoryError, ProjectPaths
from subtitle_provider import (
    SubtitleGenerationRequest,
    SubtitleGenerationResult,
    SubtitleProvider,
    SubtitleProviderError,
)


SUBTITLE_SCHEMA_VERSION = 1
TIMELINE_PATTERN = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_manifest() -> dict[str, Any]:
    return {
        "subtitle_schema_version": SUBTITLE_SCHEMA_VERSION,
        "active_version": None,
        "versions": [],
    }


class SubtitleAssetError(RuntimeError):
    """Raised when a subtitle Bundle cannot be validated or saved."""


class SubtitleAssetManager:
    """Persist each subtitle result as an immutable project-local Bundle."""

    def __init__(self, project: ProjectPaths) -> None:
        self.project = project

    def load_manifest(self) -> dict[str, Any]:
        path = self.project.subtitle_manifest_path()
        if not path.exists():
            return _new_manifest()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SubtitleAssetError(f"Subtitle manifest 无法读取：{exc}") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("versions"), list
        ):
            raise SubtitleAssetError("Subtitle manifest 结构无效。")
        if payload.get("subtitle_schema_version") != SUBTITLE_SCHEMA_VERSION:
            raise SubtitleAssetError("不支持的 Subtitle Asset Schema。")
        return payload

    def active_version(self) -> dict[str, Any] | None:
        manifest = self.load_manifest()
        active_version = manifest.get("active_version")
        for entry in manifest["versions"]:
            if entry.get("version") == active_version:
                return deepcopy(entry)
        return None

    def generate_and_save(
        self,
        request: SubtitleGenerationRequest,
        provider: SubtitleProvider,
        *,
        source_voice_version: int | None,
        source_script_path: Path | None,
        source_audio_path: Path | None,
        source_storyboard_path: Path | None = None,
    ) -> dict[str, Any]:
        if not provider.supports(request):
            raise SubtitleProviderError(
                f"Subtitle Provider {provider.provider_name} 不支持本次请求。"
            )
        result = provider.generate_subtitle(request)
        return self.save_result(
            request,
            result,
            provider.get_metadata(),
            source_voice_version=source_voice_version,
            source_script_path=source_script_path,
            source_audio_path=source_audio_path,
            source_storyboard_path=source_storyboard_path,
        )

    def save_result(
        self,
        request: SubtitleGenerationRequest,
        result: SubtitleGenerationResult,
        provider_metadata: Mapping[str, Any],
        *,
        source_voice_version: int | None,
        source_script_path: Path | None,
        source_audio_path: Path | None,
        source_storyboard_path: Path | None = None,
    ) -> dict[str, Any]:
        if request.output_format != "srt":
            raise SubtitleAssetError("Subtitle Asset v1 当前只保存 SRT。")
        self._validate_srt(result.subtitle_text, result.duration_seconds)
        manifest = self.load_manifest()
        version = max(
            (int(item["version"]) for item in manifest["versions"]),
            default=0,
        ) + 1
        version_dir = self.project.subtitle_version_dir(version)
        if version_dir.exists():
            raise SubtitleAssetError(f"Subtitle v{version:03d} 已存在，已阻止覆盖。")
        created_at = now_iso()
        staging = self.project.subtitle_staging_dir(version, uuid4().hex[:8])
        created_version = False
        try:
            staging.mkdir(parents=True, exist_ok=False)
            staging_srt = self.project.ensure_within_project(
                staging / "subtitle.srt"
            )
            staging_config = self.project.ensure_within_project(
                staging / "subtitle_config.json"
            )
            staging_srt.write_text(result.subtitle_text, encoding="utf-8")
            config = {
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "language": request.language,
                "output_format": request.output_format,
                "created_at": created_at,
                "duration": result.duration_seconds,
                "cue_count": len(result.cues),
                "source_voice_version": source_voice_version,
                "source_script_path": self._relative_optional(source_script_path),
                "source_audio_path": self._relative_optional(source_audio_path),
                "source_storyboard_path": self._relative_optional(
                    source_storyboard_path
                ),
                "source": result.metadata.get("source") or "voice_script",
                "timing_source": result.metadata.get("timing_source"),
                "settings": {
                    key: value
                    for key, value in request.settings.items()
                    if key
                    not in {
                        "compiled_storyboard",
                        "global_timeline",
                        "av_timeline_constraints",
                    }
                },
            }
            self.project.save_json(staging_config, config)
            self._validate_bundle(staging_srt, staging_config)
            staging.rename(version_dir)
            created_version = True
            entry = {
                "version": version,
                "created_at": created_at,
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "language": request.language,
                "duration_seconds": result.duration_seconds,
                "cue_count": len(result.cues),
                "source_voice_version": source_voice_version,
                "source": result.metadata.get("source") or "voice_script",
                "timing_source": result.metadata.get("timing_source"),
                "source_storyboard_path": self._relative_optional(
                    source_storyboard_path
                ),
                "subtitle_path": self._relative(
                    self.project.subtitle_version_srt_path(version)
                ),
                "config_path": self._relative(
                    self.project.subtitle_version_config_path(version)
                ),
            }
            manifest["active_version"] = version
            manifest["versions"].append(entry)
            self.project.save_json(self.project.subtitle_manifest_path(), manifest)
            return deepcopy(entry)
        except (
            OSError,
            ProjectDirectoryError,
            ValueError,
            SubtitleAssetError,
            json.JSONDecodeError,
        ) as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if created_version and version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            raise SubtitleAssetError(f"Subtitle version 保存失败：{exc}") from exc

    def _relative(self, path: Path) -> str:
        target = self.project.ensure_within_project(path)
        return target.relative_to(self.project.project_path.resolve()).as_posix()

    def _relative_optional(self, path: Path | None) -> str | None:
        return self._relative(path) if path is not None else None

    @classmethod
    def _validate_srt(cls, payload: str, duration_seconds: float) -> None:
        blocks = [block for block in payload.strip().split("\n\n") if block.strip()]
        if not blocks:
            raise SubtitleAssetError("Subtitle Provider 返回的 SRT 为空。")
        previous_end = 0
        duration_ms = int(round(duration_seconds * 1000))
        for expected_index, block in enumerate(blocks, start=1):
            lines = block.splitlines()
            if len(lines) < 3 or lines[0].strip() != str(expected_index):
                raise SubtitleAssetError("SRT 序号或文本结构无效。")
            match = TIMELINE_PATTERN.fullmatch(lines[1].strip())
            if match is None:
                raise SubtitleAssetError("SRT 时间轴格式无效。")
            values = [int(value) for value in match.groups()]
            start = cls._parts_to_ms(*values[:4])
            end = cls._parts_to_ms(*values[4:])
            if start < previous_end or end <= start or end > duration_ms:
                raise SubtitleAssetError("SRT 时间轴超出音频或存在重叠。")
            if not "\n".join(lines[2:]).strip():
                raise SubtitleAssetError("SRT 字幕文本为空。")
            previous_end = end

    @staticmethod
    def _parts_to_ms(hours: int, minutes: int, seconds: int, millis: int) -> int:
        return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis

    @classmethod
    def _validate_bundle(cls, srt_path: Path, config_path: Path) -> None:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or int(config.get("cue_count") or 0) <= 0:
            raise SubtitleAssetError("subtitle_config.json 无效。")
        cls._validate_srt(
            srt_path.read_text(encoding="utf-8"),
            float(config.get("duration") or 0),
        )
