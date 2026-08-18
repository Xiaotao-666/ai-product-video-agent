"""Provider-neutral contract for text-to-speech adapters.

This module intentionally contains no credentials, HTTP client, or concrete TTS
implementation.  A future OpenAI, ElevenLabs, or Azure adapter only needs to
implement :class:`VoiceProvider`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class VoiceProviderError(RuntimeError):
    """Raised for provider selection, capability, or generation failures."""


@dataclass(frozen=True)
class VoiceGenerationRequest:
    script: str
    voice: str
    language: str
    output_format: str = "wav"
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.script.strip():
            raise ValueError("Voice script 不能为空。")
        if not self.voice.strip():
            raise ValueError("Voice name 不能为空。")
        if not self.language.strip():
            raise ValueError("Voice language 不能为空。")
        normalized_format = self.output_format.strip().lower().lstrip(".")
        if not normalized_format:
            raise ValueError("Voice output_format 不能为空。")
        object.__setattr__(self, "output_format", normalized_format)


@dataclass(frozen=True)
class VoiceGenerationResult:
    audio_bytes: bytes
    duration_seconds: float | None = None
    provider_task_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.audio_bytes, bytes) or not self.audio_bytes:
            raise ValueError("Voice Provider 必须返回非空音频字节。")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("Voice duration_seconds 不能为负数。")


@dataclass(frozen=True)
class VoiceProviderCapabilities:
    supported_languages: frozenset[str] = frozenset()
    supported_formats: frozenset[str] = frozenset({"wav"})

    def supports(self, request: VoiceGenerationRequest) -> bool:
        language_supported = (
            not self.supported_languages
            or request.language in self.supported_languages
        )
        format_supported = (
            not self.supported_formats
            or request.output_format in self.supported_formats
        )
        return language_supported and format_supported


class VoiceProvider(ABC):
    """Stable adapter interface for all future TTS providers."""

    provider_name: str
    model_name: str
    api_version: str
    capabilities: VoiceProviderCapabilities

    def supports(self, request: VoiceGenerationRequest) -> bool:
        return self.capabilities.supports(request)

    def get_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "api_version": self.api_version,
            "supported_languages": sorted(self.capabilities.supported_languages),
            "supported_formats": sorted(self.capabilities.supported_formats),
        }

    def preflight(self, request: VoiceGenerationRequest) -> None:
        """Validate locally before an adapter is allowed to send a request."""
        if not self.supports(request):
            raise VoiceProviderError(
                f"Voice Provider {self.provider_name} 不支持 "
                f"language={request.language} 或 format={request.output_format}。"
            )

    @abstractmethod
    def generate_voice(
        self, request: VoiceGenerationRequest
    ) -> VoiceGenerationResult:
        raise NotImplementedError
