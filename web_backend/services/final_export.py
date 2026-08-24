"""Durable Web task adapter over the frozen Final Export Core."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from export_assets import ExportAssetError, ExportAssetManager
from export_pipeline import (
    ExportAlreadyExistsError,
    ExportInputs,
    ExportPipeline,
    ExportPipelineError,
)
from music_mix import MusicMixError, normalize_music_mix_settings
from project_manager import ProjectDirectoryError, ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from subtitle_assets import LEGACY_SCREEN_TEXT, NARRATION_CAPTION, SubtitleAssetError
from subtitle_generation import narration_caption_enabled
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.final_export import (
    ExportHistoryResponse,
    ExportVersionDetail,
    ExportVersionSummary,
    FinalExportExecuteRequest,
    FinalExportInputs,
    FinalExportIssue,
    FinalExportPreflightResponse,
    FinalExportSubtitle,
    FinalExportVoiceTiming,
)
from web_backend.models.postproduction import MusicMixDetail
from web_backend.models.tasks import TaskOperation, TaskRecord, TaskResultReference
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_failures import raise_task_failure
from web_backend.services.tasks import TaskService


class FinalExportWebError(RuntimeError):
    pass


class FinalExportConfirmationRequired(FinalExportWebError):
    pass


class FinalExportPreflightStale(FinalExportWebError):
    pass


class FinalExportNotReady(FinalExportWebError):
    pass


class FinalExportAlreadyCurrent(FinalExportWebError):
    pass


class ExportHistoryInvalid(FinalExportWebError):
    pass


class ExportVersionNotFound(FinalExportWebError):
    pass


class ExportVersionVideoNotFound(FinalExportWebError):
    pass


PipelineFactory = Callable[[ProjectPaths, ProjectCheckpoint], ExportPipeline]

_MIX_FIELDS = (
    "base_volume",
    "ducking_enabled",
    "ducking_ratio",
    "duck_attack_seconds",
    "duck_release_seconds",
    "fade_in_seconds",
    "fade_out_seconds",
    "loop_music",
)

_UNSAFE_PUBLIC_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|file://|api[_ -]?key|credential|"
    r"authorization|bearer\s+|sk-[a-z0-9_-]{8,})"
)
_SAFE_STATUS = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class _PreparedExport:
    response: FinalExportPreflightResponse
    paths: ProjectPaths
    checkpoint: ProjectCheckpoint
    pipeline: ExportPipeline
    selected: ExportInputs | None
    token_payload: Mapping[str, Any] | None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _public_text(value: Any, *, maximum: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > maximum or _UNSAFE_PUBLIC_TEXT.search(text):
        return None
    return text


def _status(value: Any, *, fallback: str) -> str:
    text = _public_text(value, maximum=64)
    return text if text is not None and _SAFE_STATUS.fullmatch(text) else fallback


def _mix_snapshot(value: Any, *, legacy_base_volume: float = 0.25) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _mapping(value)
    settings = raw.get("settings")
    if isinstance(settings, Mapping):
        raw = settings
    selected = {field: raw.get(field) for field in _MIX_FIELDS if raw.get(field) is not None}
    try:
        return normalize_music_mix_settings(
            selected,
            legacy_base_volume=legacy_base_volume,
        )
    except MusicMixError as error:
        raise ExportHistoryInvalid("Music Mix lineage is invalid") from error


def _mix_detail(value: Mapping[str, Any] | None) -> MusicMixDetail | None:
    if value is None:
        return None
    return MusicMixDetail(**{field: value.get(field) for field in _MIX_FIELDS})


def _voice_timing(value: Mapping[str, Any] | None) -> FinalExportVoiceTiming:
    timing = _mapping(value)
    acceptance = _mapping(timing.get("timing_acceptance"))
    return FinalExportVoiceTiming(
        status=_status(timing.get("status"), fallback="NOT_APPLICABLE"),
        accepted=bool(acceptance.get("accepted")),
        track_start=_nonnegative_number(timing.get("voice_track_start")),
        actual_audio_duration=_nonnegative_number(timing.get("actual_audio_duration")),
        actual_end=_nonnegative_number(timing.get("actual_voice_end")),
    )


class FinalExportWebService:
    """Expose safe preparation and delegate the only render to ExportPipeline."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        task_service: TaskService,
        project_lock_manager: ProjectLockManager,
        *,
        pipeline_factory: PipelineFactory | None = None,
        token_key: bytes | None = None,
    ) -> None:
        self._project_repository = project_repository
        self._task_service = task_service
        self._project_lock_manager = project_lock_manager
        self._pipeline_factory = pipeline_factory or (
            lambda paths, checkpoint: ExportPipeline(paths, checkpoint)
        )
        self._token_key = token_key or secrets.token_bytes(32)

    def preflight(self, project_id: str) -> FinalExportPreflightResponse:
        return self._prepare(project_id).response

    def submit(
        self,
        project_id: str,
        payload: FinalExportExecuteRequest,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        if payload.confirm_local_export is not True:
            raise FinalExportConfirmationRequired("local export confirmation is required")
        canonical_id = self._project_repository.get_project(project_id).project_id
        try:
            with self._project_lock_manager.project_write(canonical_id):
                prepared = self._prepare(canonical_id)
                if not prepared.response.ready:
                    raise FinalExportNotReady("Final Export is not ready")
                if not prepared.response.execution_required:
                    raise FinalExportAlreadyCurrent("Final Export is already current")
                if not self._token_matches(payload.confirmation_token, prepared):
                    raise FinalExportPreflightStale("Final Export preflight is stale")
        except ProjectLockBusy as error:
            raise ProjectBusy("project write lock is busy") from error
        return self._task_service.submit(
            project_id=canonical_id,
            operation=TaskOperation.FINAL_EXPORT,
            target_id=f"export_v{prepared.response.next_export_version:03d}",
            correlation_id=correlation_id,
            callable_=lambda: self._execute(canonical_id, payload.confirmation_token),
        )

    def history(self, project_id: str) -> ExportHistoryResponse:
        canonical_id, paths, checkpoint = self._context(project_id)
        manager = ExportAssetManager(paths)
        manifest = self._manifest(manager)
        active = _positive_int(manifest.get("active_version"))
        current, assembly_needs_update = self._current_lineage(paths, checkpoint)
        versions = [
            self._version_summary(
                paths,
                _mapping(raw),
                active=active,
                current=current,
                assembly_needs_update=assembly_needs_update,
            )
            for raw in manifest["versions"]
        ]
        versions.sort(key=lambda item: item.version, reverse=True)
        return ExportHistoryResponse(
            project_id=canonical_id,
            active_version=active,
            versions=versions,
        )

    def version(self, project_id: str, version: int) -> ExportVersionDetail:
        _canonical_id, paths, checkpoint = self._context(project_id)
        manifest = self._manifest(ExportAssetManager(paths))
        active = _positive_int(manifest.get("active_version"))
        entry = self._entry(manifest, version)
        current, assembly_needs_update = self._current_lineage(paths, checkpoint)
        summary = self._version_summary(
            paths,
            entry,
            active=active,
            current=current,
            assembly_needs_update=assembly_needs_update,
        )
        payload = self._version_manifest(paths, version)
        voice = _mapping(payload.get("voice"))
        timing = (
            FinalExportVoiceTiming(
                status=_status(
                    voice.get("calibration_status"),
                    fallback="NOT_APPLICABLE",
                ),
                accepted=bool(_mapping(voice.get("timing_acceptance")).get("accepted")),
                track_start=_nonnegative_number(voice.get("voice_track_start")),
                actual_audio_duration=_nonnegative_number(voice.get("actual_audio_duration")),
                actual_end=_nonnegative_number(voice.get("actual_voice_end")),
            )
            if voice
            else None
        )
        return ExportVersionDetail(
            **summary.model_dump(),
            voice_timing=timing,
            music_mix=_mix_detail(
                _mix_snapshot(payload.get("music_mix"))
                if payload.get("music_version") is not None
                else None
            ),
        )

    def resolve_version_video(self, project_id: str, version: int) -> Path:
        _canonical_id, paths, _checkpoint = self._context(project_id)
        manifest = self._manifest(ExportAssetManager(paths))
        self._entry(manifest, version)
        try:
            video = paths.ensure_within_project(paths.export_version_video_path(version))
            if not video.is_file() or video.stat().st_size <= 0:
                raise ExportVersionVideoNotFound("Export video is unavailable")
            return video.resolve(strict=True)
        except ExportVersionVideoNotFound:
            raise
        except (OSError, ProjectDirectoryError) as error:
            raise ExportVersionVideoNotFound("Export video is unavailable") from error

    def _execute(self, project_id: str, expected_token: str) -> TaskResultReference:
        try:
            prepared = self._prepare(project_id)
        except Exception:
            raise_task_failure(
                "EXPORT_PREFLIGHT_STALE",
                "最终导出输入已经变化，请重新检查后再次确认。",
            )
        if not prepared.response.ready:
            issue = prepared.response.issues[0]
            raise_task_failure(issue.code, issue.message)
        if not prepared.response.execution_required:
            version = prepared.response.existing_export_version
            if version is None:
                raise_task_failure(
                    "EXPORT_PREFLIGHT_STALE",
                    "最终导出输入已经变化，请重新检查后再次确认。",
                )
            return TaskResultReference(
                resource_type="FINAL_EXPORT",
                resource_id=f"export_v{version:03d}",
                version=version,
            )
        if not self._token_matches(expected_token, prepared):
            raise_task_failure(
                "EXPORT_PREFLIGHT_STALE",
                "最终导出输入已经变化，请重新检查后再次确认。",
            )
        try:
            entry = prepared.pipeline.export_current()
            version = int(entry["version"])
        except ExportAlreadyExistsError as error:
            version = int(_mapping(error.existing.get("entry")).get("version"))
        except (ExportPipelineError, ExportAssetError, ProjectStateError, ValueError):
            raise_task_failure(
                "FINAL_EXPORT_FAILED",
                "本地最终导出未能完成；不会自动重试。",
                retryable=True,
            )
        return TaskResultReference(
            resource_type="FINAL_EXPORT",
            resource_id=f"export_v{version:03d}",
            version=version,
        )

    def _prepare(self, project_id: str) -> _PreparedExport:
        canonical_id, paths, checkpoint = self._context(project_id)
        pipeline = self._pipeline_factory(paths, checkpoint)
        manager = ExportAssetManager(paths)
        manifest = self._manifest(manager)
        active_entry = manager.active_version()
        active_version = _positive_int(manifest.get("active_version"))
        next_version = self._next_version(manifest)
        issues: list[FinalExportIssue] = []
        selected: ExportInputs | None = None
        fingerprint: Mapping[str, Any] | None = None
        existing_version: int | None = None

        try:
            selected = pipeline.prepare_inputs_for_confirmation(
                pipeline.collect_inputs()
            )
        except ExportPipelineError:
            issues.append(
                FinalExportIssue(
                    code="PROJECT_NOT_READY",
                    message="当前项目没有可用于最终导出的完整 Assembly 输入。",
                )
            )

        current = self._lineage_from_inputs(selected) if selected else self._fallback_lineage(paths, checkpoint)
        current_mix = (
            _mix_snapshot(
                selected.music_mix,
                legacy_base_volume=selected.music_volume,
            )
            if selected is not None and selected.music is not None
            else None
        )
        narration_enabled = False
        try:
            narration_enabled = narration_caption_enabled(paths)
        except (SubtitleAssetError, ValueError):
            issues.append(
                FinalExportIssue(
                    code="EXPORT_INPUT_INVALID",
                    message="当前旁白配置无法安全读取。",
                )
            )

        subtitle_summary = self._subtitle_gate(
            selected.subtitle if selected else None,
            voice_version=current.voice_version,
            narration_enabled=narration_enabled,
            issues=issues,
        )
        timing = _voice_timing(selected.voice_timing if selected else None)
        if narration_enabled and current.voice_version is None:
            issues.append(
                FinalExportIssue(
                    code="ACTIVE_VOICE_REQUIRED",
                    message="当前项目启用了旁白，请先生成 active Voice。",
                )
            )
        if selected is not None:
            try:
                pipeline.validate_voice_timing(selected)
            except ExportPipelineError:
                if timing.status == "OUT_OF_TOLERANCE" and not timing.accepted:
                    issues.append(
                        FinalExportIssue(
                            code="VOICE_TIMING_ACCEPTANCE_REQUIRED",
                            message="当前配音 Timing 超出建议范围，请先在 Voice 区域明确接受。",
                        )
                    )
                elif timing.status == "OUT_OF_BOUNDS":
                    issues.append(
                        FinalExportIssue(
                            code="VOICE_OUT_OF_BOUNDS",
                            message="配音超出视频时长，无法导出。",
                        )
                    )
                else:
                    issues.append(
                        FinalExportIssue(
                            code="EXPORT_INPUT_INVALID",
                            message="当前 Voice Timing 无法通过 Export Core 校验。",
                        )
                    )

            try:
                fingerprint = pipeline.build_input_fingerprint(selected)
                existing = pipeline.find_existing_export(selected)
                if existing is not None:
                    existing_version = _positive_int(
                        _mapping(existing.get("entry")).get("version")
                    )
            except ExportPipelineError:
                issues.append(
                    FinalExportIssue(
                        code="EXPORT_INPUT_INVALID",
                        message="当前导出输入无法生成安全确认快照。",
                    )
                )

        issues = list({issue.code: issue for issue in issues}.values())
        stale_reasons = self._stale_reasons(
            _mapping(active_entry),
            current,
            assembly_needs_update=bool(checkpoint.assembly_checkpoint().get("needs_update")),
            current_mix=current_mix,
        ) if active_entry else []
        execution_required = existing_version is None
        stale = execution_required
        token_payload = (
            {
                "project_id": canonical_id,
                "input_fingerprint": deepcopy(fingerprint),
                "narration_enabled": narration_enabled,
                "subtitle_semantic_type": subtitle_summary.semantic_type,
                "subtitle_source_voice_version": subtitle_summary.source_voice_version,
                "next_export_version": next_version,
                "active_export_version": active_version,
            }
            if selected is not None and fingerprint is not None
            else None
        )
        ready = not issues
        confirmation_token = (
            self._token(token_payload)
            if ready and execution_required and token_payload is not None
            else None
        )
        response = FinalExportPreflightResponse(
            project_id=canonical_id,
            ready=ready,
            execution_required=execution_required,
            next_export_version=next_version,
            active_export_version=active_version,
            inputs=current,
            voice_timing=timing,
            subtitle=subtitle_summary,
            music_mix=_mix_detail(current_mix),
            existing_export_version=existing_version,
            stale=stale,
            stale_reasons=stale_reasons,
            issues=issues,
            confirmation_token=confirmation_token,
        )
        return _PreparedExport(
            response=response,
            paths=paths,
            checkpoint=checkpoint,
            pipeline=pipeline,
            selected=selected,
            token_payload=token_payload,
        )

    def _context(self, project_id: str) -> tuple[str, ProjectPaths, ProjectCheckpoint]:
        project = self._project_repository.get_project(project_id)
        canonical_id = project.project_id
        paths = create_project_paths(
            self._project_repository.resolve_project_dir(canonical_id),
            ensure_directories=False,
        )
        try:
            with paths.project_state_path().open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("project state is not an object")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ExportHistoryInvalid("project state is unreadable") from error
        return canonical_id, paths, ProjectCheckpoint(paths, data)

    @staticmethod
    def _manifest(manager: ExportAssetManager) -> Mapping[str, Any]:
        try:
            manifest = manager.load_manifest()
            versions = manifest.get("versions")
            if not isinstance(versions, list):
                raise ExportAssetError("Export versions are invalid")
            parsed = [_positive_int(_mapping(item).get("version")) for item in versions]
            if any(item is None for item in parsed) or len(parsed) != len(set(parsed)):
                raise ExportAssetError("Export versions are invalid")
            active = manifest.get("active_version")
            if active is not None and _positive_int(active) not in parsed:
                raise ExportAssetError("active Export version is invalid")
            return manifest
        except ExportAssetError as error:
            raise ExportHistoryInvalid("Export manifest is invalid") from error

    @staticmethod
    def _next_version(manifest: Mapping[str, Any]) -> int:
        return max(
            (_positive_int(_mapping(item).get("version")) or 0 for item in manifest["versions"]),
            default=0,
        ) + 1

    @staticmethod
    def _lineage_from_inputs(inputs: ExportInputs) -> FinalExportInputs:
        return FinalExportInputs(
            assembly_version=inputs.assembly_version,
            voice_version=_positive_int(_mapping(inputs.voice).get("version")),
            subtitle_version=_positive_int(_mapping(inputs.subtitle).get("version")),
            music_version=_positive_int(_mapping(inputs.music).get("version")),
        )

    @staticmethod
    def _fallback_lineage(paths: ProjectPaths, checkpoint: ProjectCheckpoint) -> FinalExportInputs:
        from music_assets import MusicAssetManager
        from subtitle_assets import SubtitleAssetManager
        from voice_assets import VoiceAssetManager

        try:
            voice = VoiceAssetManager(paths).active_version()
            subtitle = SubtitleAssetManager(paths).active_version()
            music = MusicAssetManager(paths).active_version()
        except Exception:
            voice = subtitle = music = None
        return FinalExportInputs(
            assembly_version=_positive_int(checkpoint.assembly_checkpoint().get("final_video_version")),
            voice_version=_positive_int(_mapping(voice).get("version")),
            subtitle_version=_positive_int(_mapping(subtitle).get("version")),
            music_version=_positive_int(_mapping(music).get("version")),
        )

    @staticmethod
    def _subtitle_gate(
        subtitle: Mapping[str, Any] | None,
        *,
        voice_version: int | None,
        narration_enabled: bool,
        issues: list[FinalExportIssue],
    ) -> FinalExportSubtitle:
        raw = _mapping(subtitle)
        source_voice = _positive_int(raw.get("source_voice_version"))
        semantic = _public_text(raw.get("semantic_type"), maximum=64)
        if semantic not in {NARRATION_CAPTION, LEGACY_SCREEN_TEXT}:
            semantic = None
        if semantic is None and raw:
            semantic = (
                NARRATION_CAPTION
                if source_voice is not None
                else LEGACY_SCREEN_TEXT
                if raw.get("source") == "compiled_storyboard"
                else None
            )
        aligned: bool | None = None
        if semantic == NARRATION_CAPTION:
            aligned = source_voice is not None and source_voice == voice_version
        if narration_enabled and raw:
            if semantic == LEGACY_SCREEN_TEXT:
                issues.append(
                    FinalExportIssue(
                        code="LEGACY_SUBTITLE_NOT_ALIGNED",
                        message="当前字幕是旧版屏幕文字，请先生成与 Voice 对齐的旁白字幕。",
                    )
                )
            elif semantic == NARRATION_CAPTION and not aligned:
                issues.append(
                    FinalExportIssue(
                        code="SUBTITLE_VOICE_MISMATCH",
                        message="当前旁白字幕与 active Voice 版本不一致，请先重新生成字幕。",
                    )
                )
        return FinalExportSubtitle(
            semantic_type=semantic,
            source_voice_version=source_voice,
            voice_aligned=aligned,
        )

    def _token(self, payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "exp_" + hmac.new(self._token_key, encoded, hashlib.sha256).hexdigest()

    def _token_matches(self, token: str, prepared: _PreparedExport) -> bool:
        if prepared.token_payload is None:
            return False
        return hmac.compare_digest(token, self._token(prepared.token_payload))

    def _current_lineage(
        self, paths: ProjectPaths, checkpoint: ProjectCheckpoint
    ) -> tuple[tuple[FinalExportInputs, dict[str, Any] | None], bool]:
        pipeline = self._pipeline_factory(paths, checkpoint)
        needs_update = bool(checkpoint.assembly_checkpoint().get("needs_update"))
        try:
            inputs = pipeline.collect_inputs()
            mix = (
                _mix_snapshot(inputs.music_mix, legacy_base_volume=inputs.music_volume)
                if inputs.music is not None
                else None
            )
            return (self._lineage_from_inputs(inputs), mix), needs_update
        except ExportPipelineError:
            return (self._fallback_lineage(paths, checkpoint), None), needs_update

    @staticmethod
    def _stale_reasons(
        entry: Mapping[str, Any],
        current: FinalExportInputs | tuple[FinalExportInputs, dict[str, Any] | None],
        *,
        assembly_needs_update: bool,
        current_mix: dict[str, Any] | None = None,
    ) -> list[str]:
        if isinstance(current, tuple):
            current, current_mix = current
        reasons: list[str] = []
        comparisons = (
            ("assembly_version", current.assembly_version, "ASSEMBLY_CHANGED"),
            ("voice_version", current.voice_version, "VOICE_CHANGED"),
            ("subtitle_version", current.subtitle_version, "SUBTITLE_CHANGED"),
            ("music_version", current.music_version, "MUSIC_CHANGED"),
        )
        unknown = False
        for field, value, code in comparisons:
            if field not in entry:
                unknown = True
            elif _positive_int(entry.get(field)) != value:
                reasons.append(code)
        if assembly_needs_update and "ASSEMBLY_CHANGED" not in reasons:
            reasons.append("ASSEMBLY_CHANGED")
        if current.music_version is not None:
            if "music_mix" not in entry:
                unknown = True
            else:
                try:
                    exported_mix = _mix_snapshot(entry.get("music_mix"))
                    if current_mix is None:
                        current_mix = exported_mix
                    if exported_mix != current_mix:
                        reasons.append("MUSIC_MIX_CHANGED")
                except ExportHistoryInvalid:
                    unknown = True
        if unknown:
            reasons.append("EXPORT_INPUT_UNKNOWN")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _entry(manifest: Mapping[str, Any], version: int) -> Mapping[str, Any]:
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ExportVersionNotFound("Export version was not found")
        matches = [
            _mapping(raw)
            for raw in manifest["versions"]
            if _positive_int(_mapping(raw).get("version")) == version
        ]
        if len(matches) != 1:
            raise ExportVersionNotFound("Export version was not found")
        return matches[0]

    @staticmethod
    def _version_manifest(paths: ProjectPaths, version: int) -> Mapping[str, Any]:
        try:
            path = paths.ensure_within_project(paths.export_version_manifest_path(version))
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, Mapping) or _positive_int(payload.get("export_version")) != version:
                raise ValueError("Export version manifest is invalid")
            return payload
        except (OSError, json.JSONDecodeError, ProjectDirectoryError, ValueError) as error:
            raise ExportHistoryInvalid("Export version detail is invalid") from error

    def _version_summary(
        self,
        paths: ProjectPaths,
        entry: Mapping[str, Any],
        *,
        active: int | None,
        current: tuple[FinalExportInputs, dict[str, Any] | None],
        assembly_needs_update: bool,
    ) -> ExportVersionSummary:
        version = _positive_int(entry.get("version"))
        if version is None:
            raise ExportHistoryInvalid("Export version is invalid")
        payload = self._version_manifest(paths, version)
        reasons = self._stale_reasons(
            entry,
            current,
            assembly_needs_update=assembly_needs_update,
        )
        try:
            video = paths.ensure_within_project(paths.export_version_video_path(version))
            video_available = video.is_file() and video.stat().st_size > 0
        except (OSError, ProjectDirectoryError):
            video_available = False
        return ExportVersionSummary(
            version=version,
            created_at=_public_text(entry.get("created_at")),
            assembly_version=_positive_int(entry.get("assembly_version")),
            voice_version=_positive_int(entry.get("voice_version")),
            subtitle_version=_positive_int(entry.get("subtitle_version")),
            music_version=_positive_int(entry.get("music_version")),
            audio_muxed=bool(payload.get("audio_muxed")),
            subtitle_burned=bool(payload.get("subtitle_burned")),
            duration_seconds=_nonnegative_number(payload.get("duration_seconds")),
            video_available=video_available,
            is_active=version == active,
            stale=bool(reasons),
            stale_reasons=reasons,
        )
