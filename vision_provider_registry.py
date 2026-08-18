"""Configurable registry and credential bootstrap for Vision Providers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from vision_provider import (
    VisionAnalysisRequest,
    VisionProvider,
    VisionProviderError,
)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("vision_provider_config.json")


def load_vision_provider_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisionProviderError(f"Vision Provider 配置无法读取：{target}：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        raise VisionProviderError("Vision Provider 配置必须包含 providers 对象。")
    return payload


def visual_understanding_enabled(path: Path | None = None) -> bool:
    """Return the A/B switch; enabled remains the backward-compatible default."""
    config = load_vision_provider_config(path)
    value = config.get("visual_understanding_enabled", True)
    if not isinstance(value, bool):
        raise VisionProviderError(
            "visual_understanding_enabled 必须是 JSON 布尔值 true 或 false。"
        )
    return value


def load_vision_credentials_from_env(
    config_path: Path | None = None,
) -> dict[str, str]:
    config = load_vision_provider_config(config_path)
    return {
        str(provider): os.getenv(str(setting.get("credential_env") or ""), "").strip()
        for provider, setting in config["providers"].items()
        if isinstance(setting, Mapping)
    }


def vision_secret_values(credentials: Mapping[str, str] | None) -> list[str]:
    return [str(value).strip() for value in dict(credentials or {}).values() if str(value).strip()]


class VisionProviderRegistry:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_vision_provider_config())
        self._providers: dict[str, VisionProvider] = {}

    def register(self, adapter: VisionProvider) -> None:
        name = str(adapter.provider_name or "").strip().lower()
        if not name:
            raise VisionProviderError("Vision Provider 必须声明 provider_name。")
        self._providers[name] = adapter

    def registered_adapters(self) -> tuple[VisionProvider, ...]:
        return tuple(self._providers.values())

    def resolve(
        self,
        request: VisionAnalysisRequest,
        provider_name: str | None = None,
    ) -> VisionProvider:
        selected = str(provider_name or self.config.get("default_provider") or "").strip().lower()
        if not selected:
            raise VisionProviderError("尚未配置默认 Vision Provider。")
        adapter = self._providers.get(selected)
        if adapter is None:
            raise VisionProviderError(f"尚未注册 Vision Provider：{selected}。")
        adapter.preflight(request)
        return adapter


def create_default_vision_registry(
    credentials: Mapping[str, str] | None = None,
    *,
    config_path: Path | None = None,
) -> VisionProviderRegistry:
    from providers.gemini_vision_provider import GeminiVisionProvider

    config = load_vision_provider_config(config_path)
    registry = VisionProviderRegistry(config)
    credentials = dict(credentials or {})
    for provider_name, raw_setting in config["providers"].items():
        if not isinstance(raw_setting, Mapping):
            continue
        adapter_name = str(raw_setting.get("adapter") or "").strip().lower()
        if adapter_name == "gemini":
            registry.register(
                GeminiVisionProvider(
                    api_key=str(credentials.get(str(provider_name)) or ""),
                    model_name=str(raw_setting.get("model") or "gemini-3.6-flash"),
                    api_version=str(raw_setting.get("api_version") or "v1beta"),
                    credential_env_name=str(
                        raw_setting.get("credential_env") or "GEMINI_API_KEY"
                    ),
                    supported_image_formats=frozenset(
                        str(item).lower()
                        for item in raw_setting.get("supported_image_formats")
                        or ("jpeg", "png", "webp")
                    ),
                )
            )
    return registry
