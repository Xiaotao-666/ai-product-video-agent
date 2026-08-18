"""Registration and selection for MusicProvider adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from music_provider import MusicAddRequest, MusicAddResult, MusicProvider, MusicProviderError


DEFAULT_CONFIG_PATH = Path(__file__).with_name("music_provider_config.json")


def load_music_provider_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MusicProviderError(
            f"Music Provider 配置无法读取：{target}：{exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise MusicProviderError("Music Provider 配置必须是 JSON 对象。")
    return payload


class MusicProviderRegistry:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_music_provider_config())
        self._providers: dict[str, MusicProvider] = {}

    def register(self, adapter: MusicProvider) -> None:
        name = str(adapter.provider_name or "").strip().lower()
        if not name:
            raise ValueError("Music Provider 必须声明 provider_name。")
        if name in self._providers:
            raise ValueError(f"Music Provider 已注册：{name}")
        self._providers[name] = adapter

    def resolve(
        self,
        request: MusicAddRequest,
        provider_name: str | None = None,
    ) -> MusicProvider:
        selected = str(
            provider_name or self.config.get("default_provider") or ""
        ).strip().lower()
        if not selected:
            raise MusicProviderError("尚未选择 Music Provider。")
        adapter = self._providers.get(selected)
        if adapter is None:
            raise MusicProviderError(f"未注册 Music Provider：{selected}")
        adapter.preflight(request)
        return adapter

    def add_music(
        self,
        request: MusicAddRequest,
        provider_name: str | None = None,
    ) -> MusicAddResult:
        return self.resolve(request, provider_name).add_music(request)

    def get_metadata(self) -> list[dict[str, Any]]:
        return [provider.get_metadata() for provider in self._providers.values()]


def build_music_provider_registry(
    config: Mapping[str, Any] | None = None,
) -> MusicProviderRegistry:
    payload = dict(config or load_music_provider_config())
    registry = MusicProviderRegistry(payload)
    settings = (payload.get("providers") or {}).get("local_music")
    if isinstance(settings, Mapping) and settings.get("enabled", True):
        from providers.local_music_provider import LocalMusicProvider

        registry.register(LocalMusicProvider.from_config(settings))
    return registry
