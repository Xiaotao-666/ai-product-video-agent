"""Thin synchronous Web adapter over the frozen local Music Core."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from fastapi import UploadFile

from music_assets import MusicAssetError, MusicAssetManager
from music_generation import add_local_music
from music_mix import MusicMixError, MusicMixSettingsManager, normalize_music_mix_settings
from music_provider import MusicProviderError
from music_provider_registry import MusicProviderRegistry, build_music_provider_registry
from post_production import PostProductionPipeline
from project_manager import ProjectDirectoryError, ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.music import (
    MusicCapabilities,
    MusicMixUpdateRequest,
    MusicOptionsResponse,
)
from web_backend.models.postproduction import MusicDetail, MusicMixDetail
from web_backend.repositories.postproduction_repository import PostProductionRepository
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.services.projects import ProjectBusy
from web_backend.services.tasks import TaskService


_COPY_CHUNK_BYTES = 1024 * 1024


class MusicWebError(RuntimeError):
    pass


class MusicFileRequired(MusicWebError):
    pass


class MusicFormatUnsupported(MusicWebError):
    pass


class MusicFileTooLarge(MusicWebError):
    pass


class MusicFileInvalid(MusicWebError):
    pass


class MusicUploadFailed(MusicWebError):
    pass


class MusicStateChanged(MusicWebError):
    pass


class MusicMixInvalid(MusicWebError):
    pass


class MusicActionNotAllowed(MusicWebError):
    pass


RegistryFactory = Callable[[], MusicProviderRegistry]


@dataclass(frozen=True)
class _MusicState:
    project_id: str
    paths: ProjectPaths
    active_version: int | None
    next_version: int
    legacy_base_volume: float


@dataclass(frozen=True)
class _MusicLimits:
    allowed_extensions: tuple[str, ...]
    max_file_size_bytes: int


def _safe_upload_suffix(filename: str | None, allowed: tuple[str, ...]) -> str:
    candidate = str(filename or "").strip()
    if not candidate:
        raise MusicFileRequired("upload filename is missing")
    decoded = candidate
    for _ in range(32):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    else:
        raise MusicFileInvalid("upload filename is unsafe")
    if (
        "\x00" in decoded
        or "/" in decoded
        or "\\" in decoded
        or ":" in decoded
        or decoded in {".", ".."}
        or decoded.startswith("..")
    ):
        raise MusicFileInvalid("upload filename is unsafe")
    suffix = Path(decoded).suffix.lower().lstrip(".")
    if suffix not in allowed:
        raise MusicFormatUnsupported("upload extension is unsupported")
    return f".{suffix}"


def _stage_upload(
    upload: UploadFile,
    directory: Path,
    *,
    allowed: tuple[str, ...],
    maximum_bytes: int,
) -> Path:
    suffix = _safe_upload_suffix(upload.filename, allowed)
    staged = directory / f"{uuid4().hex}{suffix}"
    total = 0
    try:
        upload.file.seek(0)
        with staged.open("xb") as handle:
            while True:
                chunk = upload.file.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise MusicFileTooLarge("upload exceeds Core Music limit")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except MusicWebError:
        raise
    except (OSError, ValueError) as error:
        raise MusicUploadFailed("music upload staging failed") from error
    if total <= 0:
        raise MusicFileInvalid("music upload is empty")
    return staged


def _mix_detail(settings: Mapping[str, Any]) -> MusicMixDetail:
    return MusicMixDetail(
        base_volume=settings.get("base_volume"),
        ducking_enabled=settings.get("ducking_enabled"),
        ducking_ratio=settings.get("ducking_ratio"),
        duck_attack_seconds=settings.get("duck_attack_seconds"),
        duck_release_seconds=settings.get("duck_release_seconds"),
        fade_in_seconds=settings.get("fade_in_seconds"),
        fade_out_seconds=settings.get("fade_out_seconds"),
        loop_music=settings.get("loop_music"),
        ducking_status=None,
    )


def _map_core_upload_error(error: Exception) -> MusicWebError:
    message = str(error).casefold()
    if "超过" in message or "too large" in message:
        return MusicFileTooLarge("Core rejected oversized music")
    if "不支持文件格式" in message or "unsupported" in message:
        return MusicFormatUnsupported("Core rejected music extension")
    invalid_markers = (
        "为空",
        "不匹配",
        "已损坏",
        "无法读取",
        "不存在",
        "empty",
        "signature",
        "mismatch",
    )
    if any(marker in message for marker in invalid_markers):
        return MusicFileInvalid("Core rejected invalid music data")
    return MusicUploadFailed("Core music import failed")


class MusicWebService:
    """Stage browser bytes and delegate all durable Music semantics to Core."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        postproduction_repository: PostProductionRepository,
        task_service: TaskService,
        project_lock_manager: ProjectLockManager,
        runtime_root: Path,
        *,
        registry_factory: RegistryFactory = build_music_provider_registry,
    ) -> None:
        self._project_repository = project_repository
        self._postproduction_repository = postproduction_repository
        self._task_service = task_service
        self._project_lock_manager = project_lock_manager
        self._runtime_root = Path(runtime_root)
        self._registry_factory = registry_factory

    def options(self, project_id: str) -> MusicOptionsResponse:
        state = self._state(project_id, require_ready=False)
        limits = self._limits(self._registry_factory())
        mix = self._current_mix(state)
        return MusicOptionsResponse(
            project_id=state.project_id,
            has_music=state.active_version is not None,
            active_version=state.active_version,
            next_version=state.next_version,
            allowed_extensions=list(limits.allowed_extensions),
            max_file_size_bytes=limits.max_file_size_bytes,
            mix=_mix_detail(mix),
            capabilities=MusicCapabilities(),
        )

    def upload(
        self,
        project_id: str,
        upload: UploadFile | None,
        *,
        expected_active_version: int | None,
        expected_next_version: int,
    ) -> MusicDetail:
        if upload is None:
            raise MusicFileRequired("music file is required")
        canonical_id = self._project_repository.get_project(project_id).project_id
        registry = self._registry_factory()
        limits = self._limits(registry)
        uploads_root = self._runtime_root / "music_uploads"
        try:
            uploads_root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix="web-music-upload-", dir=uploads_root
            ) as raw_directory:
                staged = _stage_upload(
                    upload,
                    Path(raw_directory),
                    allowed=limits.allowed_extensions,
                    maximum_bytes=limits.max_file_size_bytes,
                )
                with self._project_lock_manager.project_write(canonical_id):
                    self._require_no_active_task(canonical_id)
                    state = self._state(canonical_id, require_ready=True)
                    if (
                        state.active_version != expected_active_version
                        or state.next_version != expected_next_version
                    ):
                        raise MusicStateChanged("music version expectation is stale")
                    try:
                        entry = add_local_music(
                            MusicAssetManager(state.paths),
                            registry,
                            staged,
                        )
                    except (MusicProviderError, MusicAssetError, ValueError) as error:
                        raise _map_core_upload_error(error) from error
                    try:
                        checkpoint = ProjectCheckpoint.load(state.paths)
                        PostProductionPipeline(checkpoint).mark_component_completed(
                            "music",
                            version=int(entry["version"]),
                            path=str(entry["music_path"]),
                            created_at=entry.get("created_at"),
                        )
                    except (ProjectStateError, KeyError, TypeError, ValueError) as error:
                        raise MusicUploadFailed(
                            "music checkpoint update failed"
                        ) from error
        except ProjectLockBusy as error:
            raise ProjectBusy("project write lock is busy") from error
        except MusicWebError:
            raise
        except (OSError, ProjectDirectoryError) as error:
            raise MusicUploadFailed("music upload could not be persisted") from error
        return self._detail_with_current_mix(canonical_id)

    def update_mix(
        self, project_id: str, payload: MusicMixUpdateRequest
    ) -> MusicDetail:
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise MusicMixInvalid("at least one Mix field is required")
        return self._write_mix(project_id, updates=updates, reset=False)

    def reset_mix(self, project_id: str) -> MusicDetail:
        return self._write_mix(project_id, updates={}, reset=True)

    def _write_mix(
        self,
        project_id: str,
        *,
        updates: dict[str, Any],
        reset: bool,
    ) -> MusicDetail:
        canonical_id = self._project_repository.get_project(project_id).project_id
        try:
            with self._project_lock_manager.project_write(canonical_id):
                self._require_no_active_task(canonical_id)
                state = self._state(canonical_id, require_ready=True)
                if state.active_version is None:
                    raise MusicActionNotAllowed("active Music is required")
                try:
                    checkpoint = ProjectCheckpoint.load(state.paths)
                    manager = MusicMixSettingsManager(checkpoint)
                    if reset:
                        settings = manager.reset(base_volume=state.legacy_base_volume)
                    else:
                        settings = manager.update(
                            legacy_base_volume=state.legacy_base_volume,
                            **updates,
                        )
                    normalize_music_mix_settings(settings)
                except (MusicMixError, ProjectStateError, TypeError, ValueError) as error:
                    raise MusicMixInvalid("Core rejected Music Mix") from error
        except ProjectLockBusy as error:
            raise ProjectBusy("project write lock is busy") from error
        return self._detail_with_current_mix(canonical_id)

    def _detail_with_current_mix(self, project_id: str) -> MusicDetail:
        detail = self._postproduction_repository.get_music(project_id)
        state = self._state(project_id, require_ready=False)
        return detail.model_copy(update={"music_mix": _mix_detail(self._current_mix(state))})

    def _current_mix(self, state: _MusicState) -> dict[str, Any]:
        detail = self._postproduction_repository.get_music(state.project_id)
        raw = (
            detail.music_mix.model_dump(exclude={"ducking_status"}, exclude_none=True)
            if detail.music_mix is not None
            else {}
        )
        try:
            return normalize_music_mix_settings(
                raw,
                legacy_base_volume=state.legacy_base_volume,
            )
        except MusicMixError as error:
            raise MusicMixInvalid("stored Music Mix is invalid") from error

    def _state(self, project_id: str, *, require_ready: bool) -> _MusicState:
        project = self._project_repository.get_project(project_id)
        if require_ready:
            assembly = project.workflow.stages.assembly
            if assembly.status != "COMPLETED" or assembly.needs_update:
                raise MusicActionNotAllowed("Assembly is not ready")
        canonical_id = project.project_id
        paths = create_project_paths(
            self._project_repository.resolve_project_dir(canonical_id),
            ensure_directories=False,
        )
        manager = MusicAssetManager(paths)
        try:
            manifest = manager.load_manifest()
            active = manager.active_version()
            active_version = int(active["version"]) if active else None
            versions = manifest.get("versions")
            if not isinstance(versions, list):
                raise MusicAssetError("Music versions are invalid")
            parsed = [int(item["version"]) for item in versions]
            if any(version <= 0 for version in parsed) or len(parsed) != len(set(parsed)):
                raise MusicAssetError("Music versions are invalid")
        except (MusicAssetError, KeyError, TypeError, ValueError) as error:
            raise MusicUploadFailed("Music state is invalid") from error
        legacy = float(active.get("music_volume", 0.25)) if active else 0.25
        return _MusicState(
            project_id=canonical_id,
            paths=paths,
            active_version=active_version,
            next_version=max(parsed, default=0) + 1,
            legacy_base_volume=legacy,
        )

    @staticmethod
    def _limits(registry: MusicProviderRegistry) -> _MusicLimits:
        matches = [
            item
            for item in registry.get_metadata()
            if str(item.get("provider") or "") == "local_music"
        ]
        if len(matches) != 1:
            raise MusicUploadFailed("local Music provider is unavailable")
        metadata = matches[0]
        extensions = tuple(
            sorted(
                {
                    str(item).strip().lower().lstrip(".")
                    for item in metadata.get("supported_extensions", [])
                    if str(item).strip()
                }
            )
        )
        maximum = metadata.get("max_file_size_bytes")
        if not extensions or isinstance(maximum, bool):
            raise MusicUploadFailed("local Music provider metadata is invalid")
        try:
            maximum_bytes = int(maximum)
        except (TypeError, ValueError) as error:
            raise MusicUploadFailed("local Music provider limit is invalid") from error
        if maximum_bytes <= 0:
            raise MusicUploadFailed("local Music provider limit is invalid")
        return _MusicLimits(extensions, maximum_bytes)

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")
