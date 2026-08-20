"""Pure read-only projection of Assembly, post-production, and Export assets."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web_backend.models.postproduction import (
    AssemblyDetail,
    AssemblyShotVersion,
    ExportDetail,
    ExportVoiceTimingSummary,
    MusicDetail,
    MusicMixDetail,
    SubtitleCue,
    SubtitleDetail,
    VoiceCalibrationStatus,
    VoiceDetail,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
    ProjectRepositoryError,
    normalize_project_id,
)


_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_ -]?key|credential(?:_env_name)?|authorization|"
    r"provider secret|provider[_ -]?task[_ -]?id|task[_ -]?id|file[_ -]?id|"
    r"bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)
_SRT_TIMELINE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})$"
)
_PUBLIC_STATUSES = {
    "NOT_STARTED",
    "RUNNING",
    "GENERATING",
    "WAITING_REVIEW",
    "APPROVED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "FINAL_COMPLETED",
    "STALE",
    "UNKNOWN",
}
_CALIBRATION_STATUSES = {item.value for item in VoiceCalibrationStatus}
_MUSIC_MEDIA_TYPES = {
    "aac": "audio/aac",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}
_MAX_TEXT_BYTES = 1024 * 1024


class PostProductionRepositoryError(ProjectRepositoryError):
    """Base class for safe component errors translated by the HTTP layer."""


class AssemblyDataCorrupt(PostProductionRepositoryError):
    pass


class VoiceDataCorrupt(PostProductionRepositoryError):
    pass


class SubtitleDataCorrupt(PostProductionRepositoryError):
    pass


class MusicDataCorrupt(PostProductionRepositoryError):
    pass


class ExportDataCorrupt(PostProductionRepositoryError):
    pass


class AssemblyMediaNotFound(PostProductionRepositoryError):
    pass


class VoiceMediaNotFound(PostProductionRepositoryError):
    pass


class MusicMediaNotFound(PostProductionRepositoryError):
    pass


class ExportMediaNotFound(PostProductionRepositoryError):
    pass


@dataclass(frozen=True)
class ResolvedMedia:
    path: Path
    media_type: str


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _safe_text(value: Any, *, maximum: int = 50000) -> str | None:
    if value is None:
        return None
    text = str(value)
    if _WINDOWS_ABSOLUTE.search(text) or _SECRET_MARKER.search(text):
        return "[敏感内容已隐藏]"
    return text[:maximum]


def _safe_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _safe_nonnegative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _safe_ratio(value: Any) -> float | None:
    number = _safe_nonnegative_number(value)
    return number if number is not None and number <= 1 else None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_status(value: Any, *, fallback: str = "NOT_STARTED") -> str:
    normalized = str(value or fallback).strip().upper()
    return normalized if normalized in _PUBLIC_STATUSES else fallback


def _calibration_status(value: Any) -> VoiceCalibrationStatus:
    normalized = str(value or VoiceCalibrationStatus.NOT_APPLICABLE).upper()
    if normalized not in _CALIBRATION_STATUSES:
        normalized = VoiceCalibrationStatus.UNKNOWN
    return VoiceCalibrationStatus(normalized)


class PostProductionRepository:
    """Read fixed project files without importing mutable Core managers."""

    def __init__(self, project_repository: ProjectRepository) -> None:
        self.project_repository = project_repository

    def get_assembly(self, project_id: str) -> AssemblyDetail:
        api_id, project_dir, project_data = self._project_context(project_id)
        recorded = _mapping(project_data.get("assembly"))
        manifest = self._read_manifest(
            project_dir,
            ("videos", "assembly_manifest.json"),
            error_type=AssemblyDataCorrupt,
            schema_key="manifest_version",
        )
        assemblies = manifest.get("assemblies") if manifest else []
        if manifest is not None and not isinstance(assemblies, list):
            raise AssemblyDataCorrupt("assembly versions are invalid")
        version = _safe_positive_int(recorded.get("final_video_version"))
        if version is None and manifest is not None:
            version = _safe_positive_int(
                manifest.get("latest_assembly_version")
                or manifest.get("assembly_version")
            )
        active_entry: Mapping[str, Any] = {}
        if version is not None:
            for raw in assemblies if isinstance(assemblies, list) else []:
                entry = _mapping(raw)
                if _safe_positive_int(entry.get("assembly_version")) == version:
                    active_entry = entry
                    break
        has_manifest_version = bool(active_entry) or (
            version is not None
            and manifest is not None
            and _safe_positive_int(manifest.get("assembly_version")) == version
        )
        if not active_entry and has_manifest_version:
            active_entry = manifest or {}
        status = _safe_status(recorded.get("status"))
        if status == "NOT_STARTED" and version is not None and has_manifest_version:
            status = "COMPLETED"
        raw_shots = recorded.get("shot_versions")
        if not isinstance(raw_shots, list):
            raw_shots = active_entry.get("shots")
        shots: list[AssemblyShotVersion] = []
        if isinstance(raw_shots, list):
            for raw in raw_shots:
                item = _mapping(raw)
                shot_id = _safe_positive_int(item.get("shot_id"))
                video_version = _safe_positive_int(
                    item.get("approved_video_version")
                )
                if shot_id is not None and video_version is not None:
                    shots.append(
                        AssemblyShotVersion(
                            shot_id=shot_id,
                            video_version=video_version,
                        )
                    )
        return AssemblyDetail(
            project_id=api_id,
            status=status,
            current_version=version,
            needs_update=bool(recorded.get("needs_update")),
            changed_shot_id=_safe_positive_int(recorded.get("changed_shot_id")),
            created_at=_safe_text(
                recorded.get("assembled_at") or active_entry.get("created_at")
            ),
            total_duration=_safe_nonnegative_number(
                recorded.get("total_duration", active_entry.get("total_duration"))
            ),
            video_available=(
                self._assembly_media(project_dir, version, required=False) is not None
            ),
            shots=shots,
        )

    def resolve_assembly_video(self, project_id: str) -> ResolvedMedia:
        detail = self.get_assembly(project_id)
        media = self._assembly_media(
            self.project_repository.resolve_project_dir(project_id).resolve(),
            detail.current_version,
            required=True,
        )
        assert media is not None
        return media

    def get_voice(self, project_id: str) -> VoiceDetail:
        api_id, project_dir, project_data = self._project_context(project_id)
        manifest = self._read_manifest(
            project_dir,
            ("voice", "voice_manifest.json"),
            error_type=VoiceDataCorrupt,
            schema_key="voice_schema_version",
        )
        version, entry = self._active_entry(manifest, VoiceDataCorrupt)
        recorded = self._component_recorded(project_data, "voice")
        version = version or _safe_positive_int(recorded.get("active_version"))
        config = (
            self._read_json(
                project_dir,
                ("voice", "versions", f"v{version:03d}", "voice_config.json"),
                required=False,
                error_type=VoiceDataCorrupt,
            )
            if version is not None
            else None
        )
        metadata = dict(_mapping(config))
        metadata.update(entry)
        calibration = dict(_mapping(_mapping(config).get("timing_calibration")))
        calibration.update(_mapping(entry.get("timing_calibration")))
        script = (
            self._read_text(
                project_dir,
                ("voice", "versions", f"v{version:03d}", "script.txt"),
                error_type=VoiceDataCorrupt,
            )
            if version is not None
            else None
        )
        status = self._component_status(recorded, bool(entry), version)
        return VoiceDetail(
            project_id=api_id,
            status=status,
            version=version,
            created_at=_safe_text(metadata.get("created_at")),
            script=_safe_text(script),
            script_source=_safe_text(metadata.get("script_source")),
            model=_safe_text(metadata.get("model")),
            voice=_safe_text(metadata.get("voice")),
            language=_safe_text(metadata.get("language")),
            audio_available=(
                self._fixed_media(
                    project_dir,
                    ("voice", "versions", f"v{version:03d}", "audio.wav")
                    if version is not None
                    else None,
                    media_type="audio/wav",
                    required=False,
                    data_error=VoiceDataCorrupt,
                    missing_error=VoiceMediaNotFound,
                )
                is not None
            ),
            planned_narration_duration=_safe_nonnegative_number(
                metadata.get("planned_narration_duration")
            ),
            planned_first_voice_start=_safe_nonnegative_number(
                metadata.get("planned_first_voice_start")
            ),
            planned_last_voice_end=_safe_nonnegative_number(
                metadata.get("planned_last_voice_end")
            ),
            planned_voice_span=_safe_nonnegative_number(
                metadata.get("planned_voice_span")
            ),
            actual_audio_duration=_safe_nonnegative_number(
                calibration.get(
                    "actual_audio_duration", metadata.get("actual_audio_duration")
                )
            ),
            voice_track_start=_safe_nonnegative_number(
                calibration.get("voice_track_start")
            ),
            actual_voice_end=_safe_nonnegative_number(
                calibration.get("actual_voice_end")
            ),
            timing_mode=_safe_text(calibration.get("timing_mode")),
            cue_level_alignment=_optional_bool(
                calibration.get("cue_level_alignment")
            ),
            script_matches_storyboard=_optional_bool(
                calibration.get("script_matches_storyboard")
            ),
            calibration_status=_calibration_status(calibration.get("status")),
        )

    def resolve_voice_audio(self, project_id: str) -> ResolvedMedia:
        detail = self.get_voice(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        media = self._fixed_media(
            project_dir,
            ("voice", "versions", f"v{detail.version:03d}", "audio.wav")
            if detail.version is not None
            else None,
            media_type="audio/wav",
            required=True,
            data_error=VoiceDataCorrupt,
            missing_error=VoiceMediaNotFound,
        )
        assert media is not None
        return media

    def get_subtitle(self, project_id: str) -> SubtitleDetail:
        api_id, project_dir, project_data = self._project_context(project_id)
        manifest = self._read_manifest(
            project_dir,
            ("subtitles", "subtitle_manifest.json"),
            error_type=SubtitleDataCorrupt,
            schema_key="subtitle_schema_version",
        )
        version, entry = self._active_entry(manifest, SubtitleDataCorrupt)
        recorded = self._component_recorded(project_data, "subtitle")
        version = version or _safe_positive_int(recorded.get("active_version"))
        config = (
            self._read_json(
                project_dir,
                (
                    "subtitles",
                    "versions",
                    f"v{version:03d}",
                    "subtitle_config.json",
                ),
                required=False,
                error_type=SubtitleDataCorrupt,
            )
            if version is not None
            else None
        )
        metadata = dict(_mapping(config))
        metadata.update(entry)
        srt = (
            self._read_text(
                project_dir,
                ("subtitles", "versions", f"v{version:03d}", "subtitle.srt"),
                error_type=SubtitleDataCorrupt,
            )
            if version is not None
            else None
        )
        cues = self._parse_srt(srt) if srt is not None else []
        return SubtitleDetail(
            project_id=api_id,
            status=self._component_status(recorded, bool(entry), version),
            version=version,
            source=_safe_text(metadata.get("source")),
            timing_source=_safe_text(metadata.get("timing_source")),
            created_at=_safe_text(metadata.get("created_at")),
            cue_count=(
                len(cues)
                if srt is not None
                else max(0, _safe_positive_int(metadata.get("cue_count")) or 0)
            ),
            content_available=srt is not None,
            cues=cues,
        )

    def get_music(self, project_id: str) -> MusicDetail:
        api_id, project_dir, project_data = self._project_context(project_id)
        manifest = self._read_manifest(
            project_dir,
            ("music", "music_manifest.json"),
            error_type=MusicDataCorrupt,
            schema_key="music_schema_version",
        )
        version, entry = self._active_entry(manifest, MusicDataCorrupt)
        recorded = self._component_recorded(project_data, "music")
        version = version or _safe_positive_int(recorded.get("active_version"))
        config = (
            self._read_json(
                project_dir,
                ("music", "versions", f"v{version:03d}", "music_config.json"),
                required=False,
                error_type=MusicDataCorrupt,
            )
            if version is not None
            else None
        )
        metadata = dict(_mapping(config))
        metadata.update(entry)
        extension = self._music_extension(metadata) if version is not None else None
        return MusicDetail(
            project_id=api_id,
            status=self._component_status(recorded, bool(entry), version),
            version=version,
            created_at=_safe_text(metadata.get("created_at")),
            audio_available=(
                self._music_media(
                    project_dir,
                    version,
                    extension,
                    required=False,
                )
                is not None
            ),
            format=extension,
            duration_seconds=_safe_nonnegative_number(
                metadata.get("duration_seconds", metadata.get("duration"))
            ),
            music_mix=self._music_mix(
                _mapping(_mapping(project_data.get("post_production")).get("music_mix"))
            ),
        )

    def resolve_music_audio(self, project_id: str) -> ResolvedMedia:
        _, project_dir, project_data = self._project_context(project_id)
        manifest = self._read_manifest(
            project_dir,
            ("music", "music_manifest.json"),
            error_type=MusicDataCorrupt,
            schema_key="music_schema_version",
        )
        version, entry = self._active_entry(manifest, MusicDataCorrupt)
        if version is None:
            version = _safe_positive_int(
                self._component_recorded(project_data, "music").get("active_version")
            )
        config = (
            self._read_json(
                project_dir,
                ("music", "versions", f"v{version:03d}", "music_config.json"),
                required=False,
                error_type=MusicDataCorrupt,
            )
            if version is not None
            else None
        )
        metadata = dict(_mapping(config))
        metadata.update(entry)
        media = self._music_media(
            project_dir,
            version,
            self._music_extension(metadata) if version is not None else None,
            required=True,
        )
        assert media is not None
        return media

    def get_export(self, project_id: str) -> ExportDetail:
        api_id, project_dir, project_data = self._project_context(project_id)
        manifest = self._read_manifest(
            project_dir,
            ("exports", "export_manifest.json"),
            error_type=ExportDataCorrupt,
            schema_key="export_schema_version",
        )
        version, entry = self._active_entry(manifest, ExportDataCorrupt)
        recorded = self._component_recorded(project_data, "final_export")
        version = version or _safe_positive_int(recorded.get("active_version"))
        assembly = self.get_assembly(project_id)
        exported_assembly = _safe_positive_int(entry.get("assembly_version"))
        version_mismatch = bool(
            assembly.current_version is not None
            and exported_assembly is not None
            and assembly.current_version != exported_assembly
        )
        stale = bool(version is not None and (assembly.needs_update or version_mismatch))
        status = self._component_status(recorded, bool(entry), version)
        if version is not None and stale:
            status = "STALE"
        voice = _mapping(entry.get("voice"))
        voice_timing = (
            ExportVoiceTimingSummary(
                timing_mode=_safe_text(voice.get("timing_mode")),
                voice_track_start=_safe_nonnegative_number(
                    voice.get("voice_track_start")
                ),
                actual_audio_duration=_safe_nonnegative_number(
                    voice.get("actual_audio_duration")
                ),
                actual_voice_end=_safe_nonnegative_number(
                    voice.get("actual_voice_end")
                ),
                calibration_status=_calibration_status(
                    voice.get("calibration_status")
                ),
                cue_level_alignment=_optional_bool(
                    voice.get("cue_level_alignment")
                ),
            )
            if voice
            else None
        )
        return ExportDetail(
            project_id=api_id,
            status=status,
            version=version,
            created_at=_safe_text(entry.get("created_at")),
            stale=stale,
            video_available=(
                self._export_media(project_dir, version, required=False) is not None
            ),
            assembly_version=exported_assembly,
            voice_version=_safe_positive_int(entry.get("voice_version")),
            subtitle_version=_safe_positive_int(entry.get("subtitle_version")),
            music_version=_safe_positive_int(entry.get("music_version")),
            voice_timing=voice_timing,
            music_mix=self._music_mix(_mapping(entry.get("music_mix"))),
        )

    def resolve_export_video(self, project_id: str) -> ResolvedMedia:
        detail = self.get_export(project_id)
        media = self._export_media(
            self.project_repository.resolve_project_dir(project_id).resolve(),
            detail.version,
            required=True,
        )
        assert media is not None
        return media

    def _project_context(
        self, project_id: str
    ) -> tuple[str, Path, Mapping[str, Any]]:
        api_id = normalize_project_id(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        project_data = self._read_json(
            project_dir,
            ("project.json",),
            required=True,
            error_type=ProjectDataCorrupt,
        )
        assert project_data is not None
        return api_id, project_dir, project_data

    @staticmethod
    def _component_recorded(
        project_data: Mapping[str, Any], name: str
    ) -> Mapping[str, Any]:
        post = _mapping(project_data.get("post_production"))
        return _mapping(_mapping(post.get("components")).get(name))

    @staticmethod
    def _component_status(
        recorded: Mapping[str, Any], has_active_entry: bool, version: int | None
    ) -> str:
        if has_active_entry:
            return "COMPLETED"
        status = _safe_status(recorded.get("status"))
        if version is None and status not in {"RUNNING", "FAILED"}:
            return "NOT_STARTED"
        return status

    def _read_manifest(
        self,
        project_dir: Path,
        parts: tuple[str, ...],
        *,
        error_type: type[PostProductionRepositoryError],
        schema_key: str,
    ) -> Mapping[str, Any] | None:
        payload = self._read_json(
            project_dir,
            parts,
            required=False,
            error_type=error_type,
        )
        if payload is None:
            return None
        if payload.get(schema_key) != 1:
            raise error_type("unsupported component schema")
        return payload

    @staticmethod
    def _active_entry(
        manifest: Mapping[str, Any] | None,
        error_type: type[PostProductionRepositoryError],
    ) -> tuple[int | None, Mapping[str, Any]]:
        if manifest is None:
            return None, {}
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise error_type("component versions are invalid")
        raw_active = manifest.get("active_version")
        if raw_active is None:
            return None, {}
        active = _safe_positive_int(raw_active)
        if active is None:
            raise error_type("active version is invalid")
        for raw in versions:
            entry = _mapping(raw)
            if _safe_positive_int(entry.get("version")) == active:
                return active, entry
        raise error_type("active version entry is missing")

    def _read_json(
        self,
        project_dir: Path,
        parts: tuple[str, ...],
        *,
        required: bool,
        error_type: type[ProjectRepositoryError],
    ) -> Mapping[str, Any] | None:
        try:
            root = project_dir.resolve()
            path = project_dir.joinpath(*parts)
            if not path.exists():
                if path.is_symlink():
                    raise error_type("broken component JSON link")
                if required:
                    raise error_type("required component JSON is missing")
                return None
            resolved = path.resolve()
        except ProjectRepositoryError:
            raise
        except OSError as exc:
            raise error_type("component JSON path cannot be resolved") from exc
        if not path.is_file() or not _is_within(resolved, root):
            raise error_type("component JSON escaped project")
        try:
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise error_type("component JSON is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise error_type("component JSON is not an object")
        return payload

    def _read_text(
        self,
        project_dir: Path,
        parts: tuple[str, ...],
        *,
        error_type: type[PostProductionRepositoryError],
    ) -> str | None:
        try:
            root = project_dir.resolve()
            path = project_dir.joinpath(*parts)
            if not path.exists():
                if path.is_symlink():
                    raise error_type("broken component text link")
                return None
            resolved = path.resolve()
            if (
                not path.is_file()
                or not _is_within(resolved, root)
                or path.stat().st_size > _MAX_TEXT_BYTES
            ):
                raise error_type("component text is unsafe")
            return resolved.read_text(encoding="utf-8")
        except PostProductionRepositoryError:
            raise
        except (OSError, UnicodeError) as exc:
            raise error_type("component text is unreadable") from exc

    def _fixed_media(
        self,
        project_dir: Path,
        parts: tuple[str, ...] | None,
        *,
        media_type: str,
        required: bool,
        data_error: type[PostProductionRepositoryError],
        missing_error: type[PostProductionRepositoryError],
    ) -> ResolvedMedia | None:
        if parts is None:
            if required:
                raise missing_error("active media version is missing")
            return None
        try:
            root = project_dir.resolve()
            path = project_dir.joinpath(*parts)
            if not path.exists():
                if path.is_symlink():
                    raise data_error("broken media link")
                if required:
                    raise missing_error("media file is missing")
                return None
            resolved = path.resolve()
            if not path.is_file() or path.stat().st_size <= 0:
                if required:
                    raise missing_error("media file is unavailable")
                return None
        except PostProductionRepositoryError:
            raise
        except OSError as exc:
            raise data_error("media path cannot be resolved") from exc
        if not _is_within(resolved, root):
            raise data_error("media escaped project")
        return ResolvedMedia(path=resolved, media_type=media_type)

    def _assembly_media(
        self, project_dir: Path, version: int | None, *, required: bool
    ) -> ResolvedMedia | None:
        versioned = self._fixed_media(
            project_dir,
            (
                "assembly_outputs",
                f"v{version:03d}",
                "final_video.mp4",
            )
            if version is not None
            else None,
            media_type="video/mp4",
            required=False,
            data_error=AssemblyDataCorrupt,
            missing_error=AssemblyMediaNotFound,
        )
        if versioned is not None:
            return versioned
        filename = (
            "final_video.mp4"
            if version == 1
            else f"final_video_v{version:03d}.mp4"
            if version is not None
            else None
        )
        return self._fixed_media(
            project_dir,
            ("videos", filename) if filename else None,
            media_type="video/mp4",
            required=required,
            data_error=AssemblyDataCorrupt,
            missing_error=AssemblyMediaNotFound,
        )

    @staticmethod
    def _music_extension(metadata: Mapping[str, Any]) -> str:
        extension = str(metadata.get("extension") or "").strip().lower().lstrip(".")
        if extension not in _MUSIC_MEDIA_TYPES:
            raise MusicDataCorrupt("music format is unsupported")
        return extension

    def _music_media(
        self,
        project_dir: Path,
        version: int | None,
        extension: str | None,
        *,
        required: bool,
    ) -> ResolvedMedia | None:
        parts = (
            ("music", "versions", f"v{version:03d}", f"music.{extension}")
            if version is not None and extension is not None
            else None
        )
        return self._fixed_media(
            project_dir,
            parts,
            media_type=_MUSIC_MEDIA_TYPES.get(extension or "", "application/octet-stream"),
            required=required,
            data_error=MusicDataCorrupt,
            missing_error=MusicMediaNotFound,
        )

    def _export_media(
        self, project_dir: Path, version: int | None, *, required: bool
    ) -> ResolvedMedia | None:
        return self._fixed_media(
            project_dir,
            ("exports", f"v{version:03d}", "final_video.mp4")
            if version is not None
            else None,
            media_type="video/mp4",
            required=required,
            data_error=ExportDataCorrupt,
            missing_error=ExportMediaNotFound,
        )

    @staticmethod
    def _music_mix(raw: Mapping[str, Any]) -> MusicMixDetail | None:
        if not raw:
            return None
        settings = _mapping(raw.get("settings"))

        def value(name: str) -> Any:
            return raw[name] if name in raw else settings.get(name)

        return MusicMixDetail(
            base_volume=_safe_ratio(value("base_volume")),
            ducking_enabled=_optional_bool(value("ducking_enabled")),
            ducking_ratio=_safe_ratio(value("ducking_ratio")),
            duck_attack_seconds=_safe_nonnegative_number(
                value("duck_attack_seconds")
            ),
            duck_release_seconds=_safe_nonnegative_number(
                value("duck_release_seconds")
            ),
            fade_in_seconds=_safe_nonnegative_number(value("fade_in_seconds")),
            fade_out_seconds=_safe_nonnegative_number(value("fade_out_seconds")),
            loop_music=_optional_bool(value("loop_music")),
            ducking_status=_safe_text(raw.get("ducking_status"), maximum=80),
        )

    @staticmethod
    def _parse_srt(payload: str) -> list[SubtitleCue]:
        normalized = payload.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise SubtitleDataCorrupt("subtitle content is empty")
        blocks = re.split(r"\n\s*\n", normalized)
        cues: list[SubtitleCue] = []
        previous_start = -1
        for block in blocks:
            lines = block.split("\n")
            if len(lines) < 3:
                raise SubtitleDataCorrupt("subtitle cue is incomplete")
            try:
                index = int(lines[0].strip().lstrip("\ufeff"))
            except ValueError as exc:
                raise SubtitleDataCorrupt("subtitle index is invalid") from exc
            match = _SRT_TIMELINE.fullmatch(lines[1].strip())
            if index <= 0 or match is None:
                raise SubtitleDataCorrupt("subtitle timeline is invalid")
            start, end = match.groups()
            start_ms = PostProductionRepository._srt_milliseconds(start)
            end_ms = PostProductionRepository._srt_milliseconds(end)
            if start_ms < previous_start or end_ms < start_ms:
                raise SubtitleDataCorrupt("subtitle cue order is invalid")
            text = _safe_text("\n".join(lines[2:]).strip())
            if not text:
                raise SubtitleDataCorrupt("subtitle cue text is empty")
            cues.append(SubtitleCue(index=index, start=start, end=end, text=text))
            previous_start = start_ms
        return cues

    @staticmethod
    def _srt_milliseconds(value: str) -> int:
        normalized = value.replace(".", ",")
        hours, minutes, rest = normalized.split(":")
        seconds, milliseconds = rest.split(",")
        return (
            int(hours) * 3_600_000
            + int(minutes) * 60_000
            + int(seconds) * 1_000
            + int(milliseconds)
        )
