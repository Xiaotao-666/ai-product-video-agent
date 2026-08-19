"""Synchronous, project-scoped Reference Asset uploads backed by Core."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from fastapi import UploadFile

from project_manager import ProjectDirectoryError, create_project_paths
from reference_assets import (
    ALLOWED_EXTENSIONS,
    MAX_IMAGE_BYTES,
    ReferenceAssetError,
    ReferenceAssetManager,
)
from web_backend.locking.project_lock import ProjectLockBusy, ProjectLockManager
from web_backend.models.generation import ReferenceAssetUploadResponse
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetRepository,
)


_COPY_CHUNK_BYTES = 1024 * 1024


class ReferenceAssetUploadError(RuntimeError):
    """Base class for safely mapped upload failures."""


class InvalidReferenceFile(ReferenceAssetUploadError):
    pass


class UnsupportedReferenceImageFormat(ReferenceAssetUploadError):
    pass


class ReferenceImageInvalid(ReferenceAssetUploadError):
    pass


class ReferenceFileTooLarge(ReferenceAssetUploadError):
    pass


class ReferenceImportFailed(ReferenceAssetUploadError):
    pass


class ReferenceUploadBusy(ReferenceAssetUploadError):
    pass


def _safe_upload_suffix(filename: str | None) -> str:
    candidate = str(filename or "").strip()
    if not candidate:
        raise InvalidReferenceFile("upload filename is missing")
    suffix = Path(candidate).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedReferenceImageFormat("upload extension is unsupported")
    return suffix


def _stage_upload(upload: UploadFile, directory: Path) -> Path:
    suffix = _safe_upload_suffix(upload.filename)
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
                if total > MAX_IMAGE_BYTES:
                    raise ReferenceFileTooLarge("upload exceeds Core image limit")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except ReferenceAssetUploadError:
        raise
    except (OSError, ValueError) as exc:
        raise ReferenceImportFailed("upload staging failed") from exc
    if total == 0:
        raise InvalidReferenceFile("upload is empty")
    return staged


def _map_core_error(error: ReferenceAssetError) -> ReferenceAssetUploadError:
    message = str(error).casefold()
    if "only jpg" in message:
        return UnsupportedReferenceImageFormat("Core rejected image extension")
    if "20mb limit" in message:
        return ReferenceFileTooLarge("Core rejected oversized image")
    if "is empty" in message:
        return InvalidReferenceFile("Core rejected empty image")
    invalid_markers = (
        "decoded format",
        "not a readable",
        "cannot be decoded",
        "dimensions are invalid",
        "crc validation",
        "is incomplete",
        "is truncated",
    )
    if any(marker in message for marker in invalid_markers):
        return ReferenceImageInvalid("Core rejected invalid image data")
    return ReferenceImportFailed("Core reference import failed")


class ReferenceAssetUploadService:
    """Stage one multipart file, then delegate validation and storage to Core."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
        lock_manager: ProjectLockManager,
    ) -> None:
        self._project_repository = project_repository
        self._reference_repository = reference_repository
        self._lock_manager = lock_manager

    def upload(
        self, project_id: str, upload: UploadFile
    ) -> ReferenceAssetUploadResponse:
        canonical_project_id = self._project_repository.get_workflow(
            project_id
        ).project_id
        try:
            with TemporaryDirectory(prefix="web-reference-upload-") as raw_directory:
                staged = _stage_upload(upload, Path(raw_directory))
                with self._lock_manager.project_write(canonical_project_id):
                    project_dir = self._project_repository.resolve_project_dir(
                        canonical_project_id
                    )
                    paths = create_project_paths(
                        project_dir, ensure_directories=False
                    )
                    manager = ReferenceAssetManager(paths)
                    existing_ids = {
                        str(item.get("asset_id") or "")
                        for item in manager.list_assets()
                    }
                    try:
                        imported = manager.import_image(staged)
                    except ReferenceAssetError as error:
                        raise _map_core_error(error) from error
                    asset_id = str(imported.get("asset_id") or "")
                    public = self._reference_repository.asset(
                        canonical_project_id, asset_id
                    ).public()
                    return ReferenceAssetUploadResponse(
                        **public.model_dump(),
                        deduplicated=asset_id in existing_ids,
                    )
        except ProjectLockBusy as error:
            raise ReferenceUploadBusy("project reference upload lock is busy") from error
        except ReferenceAssetUploadError:
            raise
        except (OSError, ProjectDirectoryError) as error:
            raise ReferenceImportFailed("reference import could not be persisted") from error
