"""Local file adapter for user-supplied background music."""

from __future__ import annotations

import hashlib
import wave
from pathlib import Path
from typing import Any, Mapping

from music_provider import (
    MusicAddRequest,
    MusicAddResult,
    MusicProvider,
    MusicProviderCapabilities,
    MusicProviderError,
)


SUPPORTED_EXTENSIONS = frozenset({"wav", "mp3", "flac", "ogg", "m4a", "aac"})


class LocalMusicProvider(MusicProvider):
    """Validate a local audio file without calling an external service."""

    provider_name = "local_music"
    model_name = "local-file-v1"
    api_version = "local-v1"
    capabilities = MusicProviderCapabilities(
        supported_extensions=SUPPORTED_EXTENSIONS,
    )

    def __init__(self, *, max_file_size_mb: int = 500) -> None:
        self.max_file_size_bytes = int(max_file_size_mb) * 1024 * 1024

    @classmethod
    def from_config(
        cls, settings: Mapping[str, Any] | None = None
    ) -> "LocalMusicProvider":
        config = dict(settings or {})
        return cls(max_file_size_mb=int(config.get("max_file_size_mb") or 500))

    def get_metadata(self) -> dict[str, Any]:
        metadata = super().get_metadata()
        metadata.update(
            {
                "external_api": False,
                "max_file_size_bytes": self.max_file_size_bytes,
            }
        )
        return metadata

    def preflight(self, request: MusicAddRequest) -> None:
        source = request.source_path
        if not source.is_file():
            raise MusicProviderError(f"背景音乐文件不存在：{source}")
        size = source.stat().st_size
        if size <= 0:
            raise MusicProviderError("背景音乐文件为空。")
        if size > self.max_file_size_bytes:
            raise MusicProviderError(
                f"背景音乐文件超过 {self.max_file_size_bytes // (1024 * 1024)} MB。"
            )
        super().preflight(request)
        self._validate_signature(source)

    def add_music(self, request: MusicAddRequest) -> MusicAddResult:
        self.preflight(request)
        source = request.source_path
        extension = source.suffix.lower().lstrip(".")
        duration = self._wav_duration(source) if extension == "wav" else None
        return MusicAddResult(
            source_path=source,
            original_filename=source.name,
            extension=extension,
            size_bytes=source.stat().st_size,
            sha256=self._sha256(source),
            duration_seconds=duration,
            metadata={"external_api": False},
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as stream:
                rate = stream.getframerate()
                if rate <= 0:
                    raise MusicProviderError("WAV 采样率无效。")
                return round(stream.getnframes() / rate, 6)
        except (wave.Error, EOFError, OSError) as exc:
            raise MusicProviderError(f"无法读取背景音乐 WAV：{exc}") from exc

    @staticmethod
    def _validate_signature(path: Path) -> None:
        extension = path.suffix.lower().lstrip(".")
        with path.open("rb") as source:
            header = source.read(16)
        valid = {
            "wav": len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE",
            "mp3": header[:3] == b"ID3" or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0),
            "flac": header[:4] == b"fLaC",
            "ogg": header[:4] == b"OggS",
            "m4a": len(header) >= 8 and header[4:8] == b"ftyp",
            "aac": len(header) >= 2 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0,
        }.get(extension, False)
        if not valid:
            raise MusicProviderError(
                f"背景音乐内容与 .{extension} 文件格式不匹配或文件已损坏。"
            )
