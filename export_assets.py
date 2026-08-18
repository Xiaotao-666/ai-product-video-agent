"""Immutable, project-local Final Export version storage."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_manager import ProjectDirectoryError, ProjectPaths


EXPORT_SCHEMA_VERSION = 1


class ExportAssetError(RuntimeError):
    """Raised when an Export bundle cannot be safely read or committed."""


def _new_manifest() -> dict[str, Any]:
    return {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "active_version": None,
        "versions": [],
    }


class ExportAssetManager:
    """Own the global manifest and never-overwritten ``exports/vXXX`` bundles."""

    def __init__(self, project: ProjectPaths) -> None:
        self.project = project

    def load_manifest(self) -> dict[str, Any]:
        path = self.project.export_manifest_path()
        if not path.is_file():
            return _new_manifest()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExportAssetError(f"Export manifest 无法读取：{exc}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("export_schema_version") != EXPORT_SCHEMA_VERSION
            or not isinstance(payload.get("versions"), list)
        ):
            raise ExportAssetError("Export manifest 结构无效。")
        return payload

    def active_version(self) -> dict[str, Any] | None:
        manifest = self.load_manifest()
        active = manifest.get("active_version")
        for entry in manifest["versions"]:
            if entry.get("version") == active:
                return deepcopy(entry)
        return None

    def next_version(self) -> int:
        manifest = self.load_manifest()
        return max(
            (int(item.get("version", 0)) for item in manifest["versions"]),
            default=0,
        ) + 1

    def create_staging_dir(self, version: int) -> Path:
        staging = self.project.export_staging_dir(version, uuid4().hex[:8])
        try:
            staging.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise ExportAssetError(f"无法创建 Export 临时目录：{exc}") from exc
        return staging

    def commit_staging(
        self,
        *,
        version: int,
        staging_dir: Path,
        version_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a completed staging bundle, then atomically publish its pointer."""
        staging = self.project.ensure_within_project(staging_dir)
        version_dir = self.project.export_version_dir(version)
        final_video = self.project.ensure_within_project(staging / "final_video.mp4")
        if version_dir.exists():
            raise ExportAssetError(f"Export v{version:03d} 已存在，已阻止覆盖。")
        self._validate_nonempty(final_video, "Final Export 视频")

        manifest_payload = deepcopy(version_manifest)
        manifest_payload["export_schema_version"] = EXPORT_SCHEMA_VERSION
        manifest_payload["export_version"] = version
        staging_manifest = self.project.ensure_within_project(
            staging / "export_manifest.json"
        )
        self.project.save_json(staging_manifest, manifest_payload)

        global_manifest = self.load_manifest()
        committed = False
        try:
            staging.rename(version_dir)
            committed = True
            entry = {
                "version": version,
                "created_at": manifest_payload.get("created_at"),
                "final_video_path": self._relative(
                    self.project.export_version_video_path(version)
                ),
                # ``video_path`` is retained for old Resume readers.
                "video_path": self._relative(
                    self.project.export_version_video_path(version)
                ),
                "manifest_path": self._relative(
                    self.project.export_version_manifest_path(version)
                ),
                "metadata_path": self._relative(
                    self.project.export_version_manifest_path(version)
                ),
                "assembly_version": manifest_payload.get("assembly_version"),
                "video_version": manifest_payload.get("video_version"),
                "voice_version": manifest_payload.get("voice_version"),
                "voice": deepcopy(manifest_payload.get("voice")),
                "subtitle_version": manifest_payload.get("subtitle_version"),
                "music_version": manifest_payload.get("music_version"),
                "music_mix": deepcopy(manifest_payload.get("music_mix")),
                "audio_muxed": bool(manifest_payload.get("audio_muxed")),
                "subtitle_burned": bool(manifest_payload.get("subtitle_burned")),
                "input_fingerprint_sha256": manifest_payload.get(
                    "input_fingerprint_sha256"
                ),
            }
            global_manifest["active_version"] = version
            global_manifest["versions"].append(entry)
            self.project.save_json(self.project.export_manifest_path(), global_manifest)
            return deepcopy(entry)
        except (OSError, ProjectDirectoryError) as exc:
            if committed and version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            raise ExportAssetError(f"Export 版本提交失败：{exc}") from exc

    def discard_staging(self, staging_dir: Path | None) -> None:
        if staging_dir is None:
            return
        try:
            staging = self.project.ensure_within_project(staging_dir)
        except ProjectDirectoryError:
            return
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    def _relative(self, path: Path) -> str:
        target = self.project.ensure_within_project(path)
        return target.relative_to(self.project.project_path.resolve()).as_posix()

    @staticmethod
    def _validate_nonempty(path: Path, label: str) -> None:
        if not path.is_file() or path.stat().st_size <= 0:
            raise ExportAssetError(f"{label} 不存在或为空：{path}")
