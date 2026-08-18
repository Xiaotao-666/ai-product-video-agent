"""Stable provider contract shared by video generation adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from task_logger import TaskLogger
from video_generation_request import VideoGenerationRequest


class ProviderErrorCode(StrEnum):
    AUTH_ERROR = "AUTH_ERROR"
    QUOTA_ERROR = "QUOTA_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    PROVIDER_TEMPORARY_ERROR = "PROVIDER_TEMPORARY_ERROR"
    TASK_FAILED = "TASK_FAILED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"


class ProviderTaskStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VideoProviderError(RuntimeError):
    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        retryable: bool = False,
        raw_error: Any = None,
        provider_message: str | None = None,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        details = [f"{code.value}: {message}"]
        if model:
            details.append(f"Model: {model}")
        if provider_message:
            details.append(f"Provider Message: {provider_message}")
        if request_id:
            details.append(f"Request ID: {request_id}")
        super().__init__("\n".join(details))
        self.code = code
        self.message = message
        self.provider = provider
        self.model = model
        self.retryable = bool(retryable)
        self.raw_error = raw_error
        self.provider_message = provider_message
        self.request_id = request_id
        self.http_status = http_status


@dataclass(frozen=True)
class ProviderCapabilities:
    supported_visual_modes: frozenset[str]
    supported_resolutions: frozenset[str] = frozenset()
    supported_durations: frozenset[int] = frozenset()
    min_duration: int | None = None
    max_duration: int | None = None

    def supports(self, visual_mode: str) -> bool:
        return visual_mode in self.supported_visual_modes

    def supports_resolution(self, resolution: str) -> bool:
        return not self.supported_resolutions or resolution in self.supported_resolutions

    def supports_duration(self, duration: int) -> bool:
        if self.supported_durations and duration not in self.supported_durations:
            return False
        if self.min_duration is not None and duration < self.min_duration:
            return False
        if self.max_duration is not None and duration > self.max_duration:
            return False
        return True

    def as_dict(self) -> dict[str, bool]:
        modes = (
            "none",
            "first_frame",
            "reference_asset",
            "generated_keyframe",
            "first_last_frame",
            "previous_shot_frame",
        )
        return {mode: self.supports(mode) for mode in modes}


@dataclass(frozen=True)
class ProviderTask:
    provider: str | None
    model: str | None
    api_version: str | None
    generation_mode: str | None
    provider_task_id: str | None
    provider_file_id: str | None = None
    output_locator: str | None = None
    status: ProviderTaskStatus = ProviderTaskStatus.SUBMITTED
    raw_status: str | None = None
    selection_mode: str | None = None
    credential_env_name: str | None = None

    def evolve(self, **changes: Any) -> "ProviderTask":
        return replace(self, **changes)

    def bundle_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_model": self.model,
            "provider_api_version": self.api_version,
            "generation_mode": self.generation_mode,
            "provider_task_id": self.provider_task_id,
            "file_id": self.provider_file_id,
            "selection_mode": self.selection_mode,
            "credential_env_name": self.credential_env_name,
        }


@dataclass(frozen=True)
class DownloadResult:
    output_path: Path
    bytes_written: int


class VideoProvider(ABC):
    provider_name: str
    model_name: str
    api_version: str
    generation_mode_by_visual_mode: dict[str, str]
    capabilities: ProviderCapabilities
    credential_env_name: str | None = None
    credential_value: str | None = None

    def supports(self, visual_mode: str) -> bool:
        return self.capabilities.supports(visual_mode)

    def generation_mode(self, visual_mode: str) -> str:
        if not self.supports(visual_mode):
            raise VideoProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                f"{self.provider_name}/{self.model_name} 不支持 Visual Input mode={visual_mode}。",
                provider=self.provider_name,
                model=self.model_name,
            )
        return self.generation_mode_by_visual_mode[visual_mode]

    def preflight(self, request: VideoGenerationRequest) -> None:
        """Validate provider-declared capabilities without sending a request."""
        mode = request.required_capability
        self.generation_mode(mode)
        if not self.capabilities.supports_resolution(request.resolution):
            supported = ", ".join(sorted(self.capabilities.supported_resolutions))
            raise VideoProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                f"{self.model_name} 不支持 resolution={request.resolution}。"
                f"支持：{supported or '由 Adapter 决定'}。",
                provider=self.provider_name,
                model=self.model_name,
            )
        if not self.capabilities.supports_duration(request.duration):
            if self.capabilities.supported_durations:
                supported = ", ".join(
                    str(item) for item in sorted(self.capabilities.supported_durations)
                )
            else:
                supported = (
                    f"{self.capabilities.min_duration}-{self.capabilities.max_duration}"
                )
            raise VideoProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                f"{self.model_name} 不支持 duration={request.duration}。支持：{supported} 秒。",
                provider=self.provider_name,
                model=self.model_name,
            )
        if mode != "none" and not request.visual_input.get("assets"):
            raise VideoProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                f"Visual Input mode={mode} 缺少参考素材。",
                provider=self.provider_name,
                model=self.model_name,
            )

    @abstractmethod
    def submit(
        self,
        request: VideoGenerationRequest,
        task_logger: TaskLogger | None = None,
    ) -> ProviderTask:
        raise NotImplementedError

    @abstractmethod
    def poll(
        self,
        task: ProviderTask,
        task_logger: TaskLogger | None = None,
    ) -> ProviderTask:
        raise NotImplementedError

    @abstractmethod
    def download(
        self,
        task: ProviderTask,
        output_path: Path,
        request: VideoGenerationRequest,
        task_logger: TaskLogger | None = None,
    ) -> DownloadResult:
        raise NotImplementedError
