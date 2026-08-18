"""Provider-neutral contracts for multimodal reference-image understanding."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


class VisionProviderError(RuntimeError):
    """Raised when a Vision Provider is unavailable or returns invalid data."""


class VisualAnalysis(BaseModel):
    """Stable visual semantics consumed by the text planning pipeline."""

    product_identity: str = Field(min_length=1)
    brand_style: str = Field(min_length=1)
    visual_features: list[str]
    materials: list[str]
    colors: list[str]
    composition: str = Field(min_length=1)
    must_keep_elements: list[str]
    avoid_elements: list[str]


class VisualConstraints(BaseModel):
    """Compact, provider-neutral constraints consumed by prompt generation."""

    must_preserve: list[str]
    creative_freedom: list[str]
    avoid: list[str]


@dataclass(frozen=True)
class VisionAnalysisRequest:
    image_path: Path
    asset_id: str
    asset_sha256: str
    image_format: str
    product_name: str
    product_description: str
    user_notes: str = ""


@dataclass(frozen=True)
class VisionProviderCapabilities:
    supported_image_formats: frozenset[str]

    def supports(self, image_format: str) -> bool:
        normalized = str(image_format).strip().lower()
        if normalized == "jpg":
            normalized = "jpeg"
        return normalized in self.supported_image_formats


class VisionProvider(ABC):
    provider_name: str
    model_name: str
    api_version: str
    credential_env_name: str | None = None
    credential_value: str | None = None
    capabilities: VisionProviderCapabilities

    def supports(self, image_format: str) -> bool:
        return self.capabilities.supports(image_format)

    def preflight(self, request: VisionAnalysisRequest) -> None:
        if not self.supports(request.image_format):
            supported = ", ".join(sorted(self.capabilities.supported_image_formats))
            raise VisionProviderError(
                f"{self.provider_name}/{self.model_name} 不支持图片格式 "
                f"{request.image_format}；支持：{supported}。"
            )
        if self.credential_env_name and not str(self.credential_value or "").strip():
            raise VisionProviderError(
                f"{self.model_name} 尚未配置可用 API Key。\n"
                f"请在 .env 中配置：\n{self.credential_env_name}\n"
                "本次没有发送视觉分析请求。"
            )
        if not request.image_path.is_file() or request.image_path.stat().st_size <= 0:
            raise VisionProviderError(f"参考图片不存在或为空：{request.image_path}")

    @abstractmethod
    def analyze_image(self, request: VisionAnalysisRequest) -> VisualAnalysis:
        """Analyze one image and return the shared structured representation."""
        raise NotImplementedError
