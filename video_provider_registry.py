"""Provider registration, default selection, and legacy identity recovery."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_provider import (
    ProviderErrorCode,
    ProviderTask,
    VideoProvider,
    VideoProviderError,
)


DEFAULT_CONFIG_PATH = Path(__file__).with_name("video_provider_config.json")


def load_provider_credentials_from_env(
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Credential bootstrap stays in configuration code, never in project data."""
    config = load_provider_config(config_path)
    credentials: dict[str, Any] = {}
    for provider, setting in (config.get("credential_env") or {}).items():
        normalized = normalize_provider_name(provider) or str(provider)
        if isinstance(setting, Mapping):
            credentials[normalized] = {
                str(model): os.getenv(str(env_name), "").strip()
                for model, env_name in setting.items()
            }
        else:
            # Legacy provider-level config remains readable, but the checked-in
            # MiniMax config is model-level and never falls H3 back to Hailuo.
            credentials[normalized] = os.getenv(str(setting), "").strip()
    return credentials


def provider_secret_values(credentials: Mapping[str, Any] | str | None) -> list[str]:
    if isinstance(credentials, str):
        return [credentials] if credentials.strip() else []
    result: list[str] = []
    for value in dict(credentials or {}).values():
        if isinstance(value, Mapping):
            result.extend(
                str(secret).strip()
                for secret in value.values()
                if str(secret).strip()
            )
        elif str(value).strip():
            result.append(str(value).strip())
    return result


def normalize_provider_name(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    return raw or None


def load_provider_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VideoProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            f"视频 Provider 配置无法读取：{target}：{exc}",
            raw_error=repr(exc),
        ) from exc
    if not isinstance(data, dict):
        raise VideoProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            "视频 Provider 配置必须是 JSON 对象。",
        )
    return data


def infer_provider_from_model(
    model: Any, config: Mapping[str, Any] | None = None
) -> str | None:
    model_name = str(model or "").strip()
    if not model_name:
        return None
    payload = dict(config or load_provider_config())
    provider = (payload.get("known_models") or {}).get(model_name)
    return normalize_provider_name(provider)


@dataclass(frozen=True)
class ProviderRoute:
    adapter: VideoProvider
    selection_mode: str
    credential_env_name: str | None

    def metadata(self, visual_mode: str) -> dict[str, str | None]:
        return {
            "provider": normalize_provider_name(self.adapter.provider_name)
            or self.adapter.provider_name,
            "provider_model": self.adapter.model_name,
            "provider_api_version": self.adapter.api_version,
            "generation_mode": self.adapter.generation_mode(visual_mode),
            "selection_mode": self.selection_mode,
            "credential_env_name": self.credential_env_name,
        }


class VideoProviderRegistry:
    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_provider_config())
        self._providers: dict[tuple[str, str], VideoProvider] = {}
        self._defaults: dict[str, ProviderSelection] = {}
        for mode, selection in (self.config.get("default_models") or {}).items():
            self.set_default(
                str(mode),
                str(selection["provider"]),
                str(selection["model"]),
            )

    def register(self, adapter: VideoProvider) -> None:
        key = (normalize_provider_name(adapter.provider_name) or "", adapter.model_name)
        if not key[0] or not key[1]:
            raise ValueError("Provider adapter 必须声明 provider_name 和 model_name。")
        self._providers[key] = adapter

    def set_default(self, visual_mode: str, provider: str, model: str) -> None:
        normalized = normalize_provider_name(provider)
        if not normalized:
            raise ValueError("Default provider 不能为空。")
        self._defaults[str(visual_mode)] = ProviderSelection(normalized, str(model))

    def default_selection(self, visual_mode: str) -> ProviderSelection | None:
        return self._defaults.get(str(visual_mode))

    def registered_adapters(self) -> tuple[VideoProvider, ...]:
        return tuple(self._providers.values())

    def credential_env_name(self, provider: str, model: str) -> str | None:
        provider_name = normalize_provider_name(provider) or str(provider)
        setting = (self.config.get("credential_env") or {}).get(provider_name)
        if setting is None:
            # Also accept config keys that differ only by case.
            for raw_provider, raw_setting in (
                self.config.get("credential_env") or {}
            ).items():
                if normalize_provider_name(raw_provider) == provider_name:
                    setting = raw_setting
                    break
        if isinstance(setting, Mapping):
            value = setting.get(str(model))
            return str(value).strip() if value else None
        if setting:
            return str(setting).strip()
        return None

    def adapter(self, provider: str, model: str) -> VideoProvider:
        key = (normalize_provider_name(provider) or "", str(model))
        adapter = self._providers.get(key)
        if adapter is None:
            raise VideoProviderError(
                ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                f"未注册视频 Provider：provider={key[0] or '?'} model={key[1] or '?'}。",
                provider=key[0] or None,
                model=key[1] or None,
            )
        return adapter

    def resolve(
        self,
        request: VideoGenerationRequest,
        resume_task: ProviderTask | None = None,
    ) -> VideoProvider:
        return self.resolve_route(request, resume_task).adapter

    def resolve_route(
        self,
        request: VideoGenerationRequest,
        resume_task: ProviderTask | None = None,
    ) -> ProviderRoute:
        if resume_task is not None:
            adapter = self._resolve_resume(resume_task)
            selection_mode = str(resume_task.selection_mode or "legacy").lower()
        elif request.provider_selection is not None:
            adapter = self.adapter(
                request.provider_selection.provider, request.provider_selection.model
            )
            selection_mode = request.provider_selection.selection_mode
        else:
            selection = self._defaults.get(request.required_capability)
            if selection is None:
                raise VideoProviderError(
                    ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                    f"Visual Input mode={request.required_capability} 没有默认 Video Provider。",
                )
            adapter = self.adapter(selection.provider, selection.model)
            selection_mode = "auto"
        if not adapter.supports(request.required_capability):
            raise VideoProviderError(
                ProviderErrorCode.UNSUPPORTED_CAPABILITY,
                f"{adapter.provider_name}/{adapter.model_name} 不支持 "
                f"Visual Input mode={request.required_capability}。",
                provider=adapter.provider_name,
                model=adapter.model_name,
            )
        env_name = self.credential_env_name(
            adapter.provider_name, adapter.model_name
        ) or getattr(adapter, "credential_env_name", None)
        return ProviderRoute(adapter, selection_mode, env_name)

    def preflight(
        self,
        request: VideoGenerationRequest,
        resume_task: ProviderTask | None = None,
    ) -> ProviderRoute:
        route = self.resolve_route(request, resume_task)
        adapter = route.adapter
        if route.credential_env_name and not str(
            getattr(adapter, "credential_value", "") or ""
        ).strip():
            raise VideoProviderError(
                ProviderErrorCode.AUTH_ERROR,
                f"{adapter.model_name} 尚未配置可用 API Key。\n"
                f"请在 .env 中配置：\n{route.credential_env_name}\n"
                "本次没有发送视频生成请求。",
                provider=adapter.provider_name,
                model=adapter.model_name,
            )
        adapter.preflight(request)
        return route

    def _resolve_resume(self, task: ProviderTask) -> VideoProvider:
        provider = normalize_provider_name(task.provider)
        model = str(task.model or "").strip()
        if not provider and model:
            provider = infer_provider_from_model(model, self.config)
        candidates = list(self._providers.values())
        if provider:
            candidates = [
                item
                for item in candidates
                if normalize_provider_name(item.provider_name) == provider
            ]
        if model:
            candidates = [item for item in candidates if item.model_name == model]
        if task.api_version:
            candidates = [
                item for item in candidates if item.api_version == task.api_version
            ]
        if len(candidates) != 1:
            raise VideoProviderError(
                ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                "旧 Generation Bundle 的 Provider 信息不足或无法唯一识别，"
                "已阻止猜测和重复提交。",
                provider=provider,
                model=model or None,
            )
        return candidates[0]

    def provider_metadata(
        self,
        request: VideoGenerationRequest,
        resume_task: ProviderTask | None = None,
    ) -> dict[str, str | None]:
        metadata = self.resolve_route(request, resume_task).metadata(
            request.required_capability
        )
        return {
            key: metadata[key]
            for key in (
                "provider",
                "provider_model",
                "provider_api_version",
                "generation_mode",
            )
        }


def _credential_value(
    credentials: Mapping[str, Any] | str | None,
    provider: str,
    model: str,
) -> str:
    """Read injected credentials; nested maps are strictly model-scoped."""
    if isinstance(credentials, str):
        # Explicit string injection is retained for isolated legacy tests only.
        return credentials.strip()
    provider_value = dict(credentials or {}).get(provider)
    if isinstance(provider_value, Mapping):
        return str(provider_value.get(model) or "").strip()
    # Flat maps are legacy dependency injection, not the production env loader.
    return str(provider_value or "").strip()


def create_default_registry(
    credentials: Mapping[str, Any] | str | None = None,
    *,
    config_path: Path | None = None,
) -> VideoProviderRegistry:
    from providers.minimax_h3_provider import MiniMaxH3Provider
    from providers.minimax_hailuo_provider import MiniMaxHailuoProvider

    registry = VideoProviderRegistry(load_provider_config(config_path))
    hailuo_model = MiniMaxHailuoProvider.model_name
    h3_model = MiniMaxH3Provider.model_name
    registry.register(
        MiniMaxHailuoProvider(
            _credential_value(credentials, "minimax", hailuo_model),
            registry.credential_env_name("minimax", hailuo_model)
            or "MINIMAX_API_KEY",
        )
    )
    registry.register(
        MiniMaxH3Provider(
            _credential_value(credentials, "minimax", h3_model),
            registry.credential_env_name("minimax", h3_model)
            or "MINIMAX_H3_API_KEY",
        )
    )
    return registry
