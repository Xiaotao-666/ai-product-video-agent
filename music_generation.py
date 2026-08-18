"""Local background-music orchestration and future Export input snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from music_assets import MusicAssetError, MusicAssetManager
from music_provider import MusicAddRequest
from music_provider_registry import MusicProviderRegistry
from project_manager import ProjectPaths


@dataclass(frozen=True)
class MusicExportInput:
    version: int
    music_path: Path
    music_volume: float
    extension: str
    sha256: str


def add_local_music(
    manager: MusicAssetManager,
    registry: MusicProviderRegistry,
    source_path: str | Path,
    *,
    music_volume: float = 0.25,
    provider_name: str | None = None,
) -> dict:
    request = MusicAddRequest(
        source_path=Path(str(source_path).strip().strip('"')),
        music_volume=music_volume,
    )
    provider = registry.resolve(request, provider_name)
    return manager.add_and_save(request, provider)


def load_active_music_export_input(paths: ProjectPaths) -> MusicExportInput | None:
    """Return a provider-neutral snapshot; no audio mixing is performed here."""
    active = MusicAssetManager(paths).active_version()
    if active is None:
        return None
    music_path = paths.ensure_within_project(
        paths.project_path / str(active.get("music_path") or "")
    )
    if not music_path.is_file() or music_path.stat().st_size <= 0:
        raise MusicAssetError("当前 Music version 的音频文件不存在。")
    return MusicExportInput(
        version=int(active["version"]),
        music_path=music_path,
        music_volume=float(active.get("music_volume", 0.25)),
        extension=str(active.get("extension") or ""),
        sha256=str(active.get("sha256") or ""),
    )
