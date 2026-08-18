"""Provider-neutral contracts for subtitle generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class SubtitleProviderError(RuntimeError):
    """Raised for subtitle capability, validation, or generation failures."""


@dataclass(frozen=True)
class SubtitleGenerationRequest:
    script: str
    audio_duration_seconds: float | None
    language: str = "zh-CN"
    output_format: str = "srt"
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.script.strip():
            raise ValueError("Subtitle script 不能为空。")
        if not self.language.strip():
            raise ValueError("Subtitle language 不能为空。")
        output_format = self.output_format.strip().lower().lstrip(".")
        if not output_format:
            raise ValueError("Subtitle output_format 不能为空。")
        if (
            self.audio_duration_seconds is not None
            and float(self.audio_duration_seconds) <= 0
        ):
            raise ValueError("Subtitle audio duration 必须大于 0。")
        object.__setattr__(self, "output_format", output_format)


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    text: str

    def __post_init__(self) -> None:
        if self.index <= 0:
            raise ValueError("Subtitle cue index 必须大于 0。")
        if self.start_seconds < 0 or self.end_seconds <= self.start_seconds:
            raise ValueError("Subtitle cue 时间轴无效。")
        if not self.text.strip():
            raise ValueError("Subtitle cue text 不能为空。")


@dataclass(frozen=True)
class SubtitleGenerationResult:
    subtitle_text: str
    cues: tuple[SubtitleCue, ...]
    duration_seconds: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subtitle_text.strip() or not self.cues:
            raise ValueError("Subtitle Provider 必须返回非空字幕。")
        if self.duration_seconds <= 0:
            raise ValueError("Subtitle duration 必须大于 0。")


@dataclass(frozen=True)
class SubtitleProviderCapabilities:
    supported_languages: frozenset[str] = frozenset()
    supported_formats: frozenset[str] = frozenset({"srt"})

    def supports(self, request: SubtitleGenerationRequest) -> bool:
        language_supported = (
            not self.supported_languages
            or request.language in self.supported_languages
        )
        format_supported = (
            not self.supported_formats
            or request.output_format in self.supported_formats
        )
        return language_supported and format_supported


class SubtitleProvider(ABC):
    """Stable interface for local or future external subtitle providers."""

    provider_name: str
    model_name: str
    api_version: str
    capabilities: SubtitleProviderCapabilities

    def supports(self, request: SubtitleGenerationRequest) -> bool:
        return self.capabilities.supports(request)

    def get_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "api_version": self.api_version,
            "supported_languages": sorted(self.capabilities.supported_languages),
            "supported_formats": sorted(self.capabilities.supported_formats),
        }

    def preflight(self, request: SubtitleGenerationRequest) -> None:
        if not self.supports(request):
            raise SubtitleProviderError(
                f"Subtitle Provider {self.provider_name} 不支持 "
                f"language={request.language} 或 format={request.output_format}。"
            )

    @abstractmethod
    def generate_subtitle(
        self, request: SubtitleGenerationRequest
    ) -> SubtitleGenerationResult:
        raise NotImplementedError
