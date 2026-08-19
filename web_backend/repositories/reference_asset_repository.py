"""Strictly read-only access to project reference images."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reference_assets import ReferenceAssetError, inspect_image
from web_backend.models.generation import (
    ReferenceAssetListResponse,
    ReferenceAssetPublic,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
    ProjectRepositoryError,
)


_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPES = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


class ReferenceAssetRepositoryError(ProjectRepositoryError):
    pass


class InvalidReferenceAssetId(ReferenceAssetRepositoryError):
    pass


class ReferenceAssetNotFound(ReferenceAssetRepositoryError):
    pass


class ReferenceAssetDataCorrupt(ReferenceAssetRepositoryError):
    pass


@dataclass(frozen=True)
class ReferenceAssetRecord:
    asset_id: str
    filename: str
    media_type: str
    width: int
    height: int
    sha256: str
    project_path: str
    source: str
    path: Path

    def public(self) -> ReferenceAssetPublic:
        return ReferenceAssetPublic(
            asset_id=self.asset_id,
            filename=self.filename,
            media_type=self.media_type,
            width=self.width,
            height=self.height,
        )

    def core_record(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "filename": self.filename,
            "project_path": self.project_path,
            "sha256": self.sha256,
            "source": self.source,
        }


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class ReferenceAssetRepository:
    """Read fixed manifest entries without constructing the mutable Core manager."""

    def __init__(self, project_repository: ProjectRepository) -> None:
        self.project_repository = project_repository

    def list_assets(self, project_id: str) -> ReferenceAssetListResponse:
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        records = [self._record(project_dir, raw) for raw in self._assets(project_dir)]
        return ReferenceAssetListResponse(
            project_id=self.project_repository.get_workflow(project_id).project_id,
            assets=[record.public() for record in records],
        )

    def asset(self, project_id: str, asset_id: str) -> ReferenceAssetRecord:
        normalized = self._normalize_asset_id(asset_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        for raw in self._assets(project_dir):
            if str(raw.get("asset_id") or "") == normalized:
                return self._record(project_dir, raw)
        raise ReferenceAssetNotFound("reference asset was not found")

    def resolve_image(self, project_id: str, asset_id: str) -> tuple[Path, str]:
        record = self.asset(project_id, asset_id)
        return record.path, record.media_type

    @staticmethod
    def _normalize_asset_id(value: str) -> str:
        candidate = str(value or "").strip()
        if not _ASSET_ID.fullmatch(candidate):
            raise InvalidReferenceAssetId("unsafe reference asset id")
        return candidate

    def _assets(self, project_dir: Path) -> list[Mapping[str, Any]]:
        manifest = project_dir / "references" / "reference_manifest.json"
        if not manifest.exists():
            if manifest.is_symlink():
                raise ReferenceAssetDataCorrupt("broken reference manifest link")
            return []
        try:
            resolved = manifest.resolve()
        except OSError as exc:
            raise ReferenceAssetDataCorrupt("reference manifest cannot be resolved") from exc
        if manifest.is_symlink() or not manifest.is_file() or not _within(resolved, project_dir):
            raise ReferenceAssetDataCorrupt("reference manifest escaped project")
        try:
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReferenceAssetDataCorrupt("reference manifest is unreadable") from exc
        assets = payload.get("assets") if isinstance(payload, Mapping) else None
        if not isinstance(assets, list) or any(not isinstance(item, Mapping) for item in assets):
            raise ReferenceAssetDataCorrupt("reference manifest format is invalid")
        return list(assets)

    def _record(
        self, project_dir: Path, raw: Mapping[str, Any]
    ) -> ReferenceAssetRecord:
        try:
            asset_id = self._normalize_asset_id(str(raw.get("asset_id") or ""))
        except InvalidReferenceAssetId as exc:
            raise ReferenceAssetDataCorrupt(
                "reference asset metadata is invalid"
            ) from exc
        filename = str(raw.get("filename") or "").strip()
        relative = str(raw.get("project_path") or "").strip()
        expected_digest = str(raw.get("sha256") or "").strip().lower()
        if (
            not filename
            or Path(filename).name != filename
            or not relative
            or not _SHA256.fullmatch(expected_digest)
        ):
            raise ReferenceAssetDataCorrupt("reference asset metadata is invalid")
        try:
            references_root = (project_dir / "references" / "project").resolve()
            candidate = project_dir / Path(relative)
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ReferenceAssetDataCorrupt("reference asset path cannot be resolved") from exc
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or resolved.parent != references_root
            or not _within(resolved, project_dir)
            or resolved.name != filename
        ):
            raise ReferenceAssetDataCorrupt("reference asset escaped its fixed directory")
        try:
            image_type, width, height = inspect_image(resolved)
            actual_digest = _digest(resolved)
        except (OSError, ReferenceAssetError) as exc:
            raise ReferenceAssetDataCorrupt("reference image is unreadable") from exc
        if actual_digest != expected_digest or image_type not in _MEDIA_TYPES:
            raise ReferenceAssetDataCorrupt("reference image verification failed")
        return ReferenceAssetRecord(
            asset_id=asset_id,
            filename=filename,
            media_type=_MEDIA_TYPES[image_type],
            width=width,
            height=height,
            sha256=actual_digest,
            project_path=resolved.relative_to(project_dir).as_posix(),
            source=str(raw.get("source") or "user_upload"),
            path=resolved,
        )
