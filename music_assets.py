"""Immutable project-local storage for uploaded background music."""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from music_provider import MusicAddRequest, MusicAddResult, MusicProvider
from project_manager import ProjectDirectoryError, ProjectPaths


MUSIC_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_manifest() -> dict[str, Any]:
    return {
        "music_schema_version": MUSIC_SCHEMA_VERSION,
        "active_version": None,
        "versions": [],
    }


class MusicAssetError(RuntimeError):
    """Raised when a Music Bundle cannot be validated or saved."""


class MusicAssetManager:
    """Copy validated local music into immutable project versions."""

    def __init__(self, project: ProjectPaths) -> None:
        self.project = project

    def load_manifest(self) -> dict[str, Any]:
        path = self.project.music_manifest_path()
        if not path.exists():
            return _new_manifest()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MusicAssetError(f"Music manifest 无法读取：{exc}") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("versions"), list
        ):
            raise MusicAssetError("Music manifest 结构无效。")
        if payload.get("music_schema_version") != MUSIC_SCHEMA_VERSION:
            raise MusicAssetError("不支持的 Music Asset Schema。")
        return payload

    def active_version(self) -> dict[str, Any] | None:
        manifest = self.load_manifest()
        active_version = manifest.get("active_version")
        for entry in manifest["versions"]:
            if entry.get("version") == active_version:
                return deepcopy(entry)
        return None

    def add_and_save(
        self,
        request: MusicAddRequest,
        provider: MusicProvider,
    ) -> dict[str, Any]:
        result = provider.add_music(request)
        return self.save_result(request, result, provider.get_metadata())

    def save_result(
        self,
        request: MusicAddRequest,
        result: MusicAddResult,
        provider_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._sha256(result.source_path) != result.sha256:
            raise MusicAssetError("背景音乐在校验后发生变化，已阻止导入。")
        manifest = self.load_manifest()
        version = max(
            (int(item["version"]) for item in manifest["versions"]),
            default=0,
        ) + 1
        version_dir = self.project.music_version_dir(version)
        if version_dir.exists():
            raise MusicAssetError(f"Music v{version:03d} 已存在，已阻止覆盖。")
        asset_path = self.project.music_asset_path(result.sha256, result.extension)
        staging = self.project.music_staging_dir(version, uuid4().hex[:8])
        created_asset = False
        created_version = False
        created_at = now_iso()
        try:
            if asset_path.exists():
                if self._sha256(asset_path) != result.sha256:
                    raise MusicAssetError("已有 Music Asset 的 SHA-256 不匹配。")
            else:
                shutil.copy2(result.source_path, asset_path)
                created_asset = True
                if self._sha256(asset_path) != result.sha256:
                    raise MusicAssetError("Music Asset 复制校验失败。")

            staging.mkdir(parents=True, exist_ok=False)
            staging_music = self.project.ensure_within_project(
                staging / f"music.{result.extension}"
            )
            staging_config = self.project.ensure_within_project(
                staging / "music_config.json"
            )
            shutil.copy2(asset_path, staging_music)
            config = {
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "created_at": created_at,
                "music_volume": request.music_volume,
                "original_filename": result.original_filename,
                "extension": result.extension,
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
                "duration": result.duration_seconds,
                "settings": dict(request.settings),
            }
            self.project.save_json(staging_config, config)
            self._validate_copy(staging_music, result.sha256)
            staging.rename(version_dir)
            created_version = True
            entry = {
                "version": version,
                "created_at": created_at,
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "music_volume": request.music_volume,
                "original_filename": result.original_filename,
                "extension": result.extension,
                "sha256": result.sha256,
                "size_bytes": result.size_bytes,
                "duration_seconds": result.duration_seconds,
                "asset_path": self._relative(asset_path),
                "music_path": self._relative(
                    self.project.music_version_audio_path(
                        version, result.extension
                    )
                ),
                "config_path": self._relative(
                    self.project.music_version_config_path(version)
                ),
            }
            manifest["active_version"] = version
            manifest["versions"].append(entry)
            self.project.save_json(self.project.music_manifest_path(), manifest)
            return deepcopy(entry)
        except (OSError, ProjectDirectoryError, ValueError, MusicAssetError) as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if created_version and version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            if created_asset and asset_path.exists():
                asset_path.unlink(missing_ok=True)
            raise MusicAssetError(f"Music version 保存失败：{exc}") from exc

    def _relative(self, path: Path) -> str:
        target = self.project.ensure_within_project(path)
        return target.relative_to(self.project.project_path.resolve()).as_posix()

    @classmethod
    def _validate_copy(cls, path: Path, expected_sha256: str) -> None:
        if not path.is_file() or path.stat().st_size <= 0:
            raise MusicAssetError("Music 文件复制结果为空。")
        if cls._sha256(path) != expected_sha256:
            raise MusicAssetError("Music version SHA-256 校验失败。")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
