"""Registration and selection for VoiceProvider adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderError,
)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("voice_provider_config.json")


def normalize_voice_provider_name(value: Any) -> str:
    return str(value or "").strip().lower()


def load_voice_provider_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoiceProviderError(
            f"Voice Provider 配置无法读取：{target}：{exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise VoiceProviderError("Voice Provider 配置必须是 JSON 对象。")
    return payload


class VoiceProviderRegistry:
    """A registry isolated from the existing VideoProviderRegistry."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_voice_provider_config())
        self._providers: dict[str, VoiceProvider] = {}

    def register(self, adapter: VoiceProvider) -> None:
        name = normalize_voice_provider_name(adapter.provider_name)
        if not name:
            raise ValueError("Voice Provider 必须声明 provider_name。")
        if name in self._providers:
            raise ValueError(f"Voice Provider 已注册：{name}")
        self._providers[name] = adapter

    def registered_providers(self) -> tuple[VoiceProvider, ...]:
        return tuple(self._providers.values())

    def resolve(
        self,
        request: VoiceGenerationRequest,
        provider_name: str | None = None,
    ) -> VoiceProvider:
        requested = normalize_voice_provider_name(provider_name)
        selected = requested or normalize_voice_provider_name(
            self.config.get("default_provider")
        )
        if not selected:
            raise VoiceProviderError(
                "尚未选择 Voice Provider；当前基础架构不会自动调用真实 TTS。"
            )
        adapter = self._providers.get(selected)
        if adapter is None:
            raise VoiceProviderError(f"未注册 Voice Provider：{selected}")
        if not adapter.supports(request):
            raise VoiceProviderError(
                f"Voice Provider {selected} 不支持 language={request.language} "
                f"或 format={request.output_format}。"
            )
        return adapter

    def generate_voice(
        self,
        request: VoiceGenerationRequest,
        provider_name: str | None = None,
    ) -> VoiceGenerationResult:
        return self.resolve(request, provider_name).generate_voice(request)

    def preflight(
        self,
        request: VoiceGenerationRequest,
        provider_name: str | None = None,
    ) -> VoiceProvider:
        adapter = self.resolve(request, provider_name)
        adapter.preflight(request)
        return adapter

    def get_metadata(self) -> list[dict[str, Any]]:
        return [adapter.get_metadata() for adapter in self.registered_providers()]


def build_voice_provider_registry(
    config: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    token_fetcher: Any = None,
    http_post: Any = None,
    xfyun_websocket_factory: Any = None,
    xfyun_date_factory: Any = None,
) -> VoiceProviderRegistry:
    """Build configured built-ins while keeping credentials out of config files."""
    payload = dict(config or load_voice_provider_config())
    registry = VoiceProviderRegistry(payload)
    source_environ = os.environ if environ is None else environ
    provider_settings = payload.get("providers") or {}
    aliyun_settings = provider_settings.get("aliyun_tts")
    if isinstance(aliyun_settings, Mapping) and aliyun_settings.get("enabled", True):
        from providers.aliyun_tts_provider import AliyunTTSProvider

        registry.register(
            AliyunTTSProvider.from_env(
                aliyun_settings,
                environ=source_environ,
                token_fetcher=token_fetcher,
                http_post=http_post,
            )
        )
    xfyun_settings = provider_settings.get("xfyun_tts")
    if isinstance(xfyun_settings, Mapping) and xfyun_settings.get("enabled", True):
        from providers.xfyun_tts_provider import XfyunTTSProvider

        registry.register(
            XfyunTTSProvider.from_env(
                xfyun_settings,
                environ=source_environ,
                websocket_factory=xfyun_websocket_factory,
                date_factory=xfyun_date_factory,
            )
        )
    return registry
