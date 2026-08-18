"""Provider-neutral contracts for project background music."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class MusicProviderError(RuntimeError):
    """Raised for music capability, validation, or import failures."""


@dataclass(frozen=True)
class MusicAddRequest:
    source_path: Path
    music_volume: float = 0.25
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source = Path(self.source_path).expanduser().resolve()
        volume = float(self.music_volume)
        if not 0.0 <= volume <= 1.0:
            raise ValueError("music_volume 必须在 0.0 到 1.0 之间。")
        object.__setattr__(self, "source_path", source)
        object.__setattr__(self, "music_volume", volume)


@dataclass(frozen=True)
class MusicAddResult:
    source_path: Path
    original_filename: str
    extension: str
    size_bytes: int
    sha256: str
    duration_seconds: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_path.is_file() or self.size_bytes <= 0:
            raise ValueError("Music Provider 必须返回有效的本地音乐文件。")
        if len(self.sha256) != 64:
            raise ValueError("Music Provider 必须返回 SHA-256。")


@dataclass(frozen=True)
class MusicProviderCapabilities:
    supported_extensions: frozenset[str]

    def supports(self, request: MusicAddRequest) -> bool:
        return request.source_path.suffix.lower().lstrip(".") in self.supported_extensions


class MusicProvider(ABC):
    provider_name: str
    model_name: str
    api_version: str
    capabilities: MusicProviderCapabilities

    def supports(self, request: MusicAddRequest) -> bool:
        return self.capabilities.supports(request)

    def get_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "api_version": self.api_version,
            "supported_extensions": sorted(self.capabilities.supported_extensions),
        }

    def preflight(self, request: MusicAddRequest) -> None:
        if not self.supports(request):
            raise MusicProviderError(
                f"Music Provider {self.provider_name} 不支持文件格式 "
                f"{request.source_path.suffix or '(无扩展名)'}。"
            )

    @abstractmethod
    def add_music(self, request: MusicAddRequest) -> MusicAddResult:
        raise NotImplementedError
