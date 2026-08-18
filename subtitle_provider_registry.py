"""Registration and selection for SubtitleProvider adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from subtitle_provider import (
    SubtitleGenerationRequest,
    SubtitleGenerationResult,
    SubtitleProvider,
    SubtitleProviderError,
)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("subtitle_provider_config.json")


def load_subtitle_provider_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubtitleProviderError(
            f"Subtitle Provider 配置无法读取：{target}：{exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SubtitleProviderError("Subtitle Provider 配置必须是 JSON 对象。")
    return payload


class SubtitleProviderRegistry:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_subtitle_provider_config())
        self._providers: dict[str, SubtitleProvider] = {}

    def register(self, adapter: SubtitleProvider) -> None:
        name = str(adapter.provider_name or "").strip().lower()
        if not name:
            raise ValueError("Subtitle Provider 必须声明 provider_name。")
        if name in self._providers:
            raise ValueError(f"Subtitle Provider 已注册：{name}")
        self._providers[name] = adapter

    def resolve(
        self,
        request: SubtitleGenerationRequest,
        provider_name: str | None = None,
    ) -> SubtitleProvider:
        if provider_name is None:
            priority = self.config.get("routing_priority") or []
            if not isinstance(priority, list):
                raise SubtitleProviderError(
                    "Subtitle Provider routing_priority 必须是数组。"
                )
            for raw_name in priority:
                routed_name = str(raw_name or "").strip().lower()
                adapter = self._providers.get(routed_name)
                if adapter is not None and adapter.supports(request):
                    adapter.preflight(request)
                    return adapter
        selected = str(
            provider_name or self.config.get("default_provider") or ""
        ).strip().lower()
        if not selected:
            raise SubtitleProviderError("尚未选择 Subtitle Provider。")
        adapter = self._providers.get(selected)
        if adapter is None:
            raise SubtitleProviderError(f"未注册 Subtitle Provider：{selected}")
        adapter.preflight(request)
        return adapter

    def generate_subtitle(
        self,
        request: SubtitleGenerationRequest,
        provider_name: str | None = None,
    ) -> SubtitleGenerationResult:
        return self.resolve(request, provider_name).generate_subtitle(request)

    def get_metadata(self) -> list[dict[str, Any]]:
        return [provider.get_metadata() for provider in self._providers.values()]


def build_subtitle_provider_registry(
    config: Mapping[str, Any] | None = None,
) -> SubtitleProviderRegistry:
    payload = dict(config or load_subtitle_provider_config())
    registry = SubtitleProviderRegistry(payload)
    storyboard_settings = (payload.get("providers") or {}).get(
        "storyboard_subtitle"
    )
    if isinstance(storyboard_settings, Mapping) and storyboard_settings.get(
        "enabled", True
    ):
        from providers.storyboard_subtitle_provider import (
            StoryboardSubtitleProvider,
        )

        registry.register(
            StoryboardSubtitleProvider.from_config(storyboard_settings)
        )
    settings = (payload.get("providers") or {}).get("script_subtitle")
    if isinstance(settings, Mapping) and settings.get("enabled", True):
        from providers.script_subtitle_provider import ScriptSubtitleProvider

        registry.register(ScriptSubtitleProvider.from_config(settings))
    return registry
