"""Alibaba Cloud Intelligent Speech Interaction (NLS) REST TTS adapter."""

from __future__ import annotations

import json
import importlib.util
from typing import Any, Callable, Mapping

import requests

from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
    VoiceProviderError,
)


REGION_ENDPOINTS = {
    "cn-shanghai": "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts",
    "cn-beijing": "https://nls-gateway-cn-beijing.aliyuncs.com/stream/v1/tts",
    "cn-shenzhen": "https://nls-gateway-cn-shenzhen.aliyuncs.com/stream/v1/tts",
}
TOKEN_REGION = "cn-shanghai"
TOKEN_DOMAIN = "nls-meta.cn-shanghai.aliyuncs.com"
TOKEN_API_VERSION = "2019-02-28"


class AliyunTTSProvider(VoiceProvider):
    """Short-text NLS REST synthesis with AccessKey-derived token auth."""

    provider_name = "aliyun_tts"
    api_version = "nls-stream-v1"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        app_key: str,
        region: str,
        model: str = "nls-stream-tts",
        sample_rate: int = 16000,
        timeout_seconds: float = 60.0,
        token_fetcher: Callable[[str, str], str] | None = None,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self.access_key_id = str(access_key_id or "").strip()
        self.access_key_secret = str(access_key_secret or "").strip()
        self.app_key = str(app_key or "").strip()
        self.region = str(region or "").strip().lower()
        self.model_name = str(model or "nls-stream-tts").strip()
        self.sample_rate = int(sample_rate)
        self.timeout_seconds = float(timeout_seconds)
        self._uses_default_token_fetcher = token_fetcher is None
        self._token_fetcher = token_fetcher or self._fetch_token_with_sdk
        self._http_post = http_post or requests.post

    @classmethod
    def from_env(
        cls,
        settings: Mapping[str, Any] | None = None,
        *,
        environ: Mapping[str, str],
        token_fetcher: Callable[[str, str], str] | None = None,
        http_post: Callable[..., Any] | None = None,
    ) -> "AliyunTTSProvider":
        config = dict(settings or {})
        return cls(
            access_key_id=environ.get("ALIYUN_ACCESS_KEY_ID", ""),
            access_key_secret=environ.get("ALIYUN_ACCESS_KEY_SECRET", ""),
            app_key=environ.get("ALIYUN_TTS_APP_KEY", ""),
            region=environ.get("ALIYUN_TTS_REGION", ""),
            model=str(config.get("model") or "nls-stream-tts"),
            sample_rate=int(config.get("sample_rate") or 16000),
            token_fetcher=token_fetcher,
            http_post=http_post,
        )

    @property
    def endpoint(self) -> str | None:
        return REGION_ENDPOINTS.get(self.region)

    def supports(self, request: VoiceGenerationRequest) -> bool:
        return super().supports(request)

    def get_metadata(self) -> dict[str, Any]:
        metadata = super().get_metadata()
        metadata.update(
            {
                "region": self.region or None,
                "endpoint": self.endpoint,
                "sample_rate": self.sample_rate,
                "max_text_characters": 300,
                "credential_env_names": [
                    "ALIYUN_ACCESS_KEY_ID",
                    "ALIYUN_ACCESS_KEY_SECRET",
                    "ALIYUN_TTS_APP_KEY",
                    "ALIYUN_TTS_REGION",
                ],
            }
        )
        return metadata

    def preflight(self, request: VoiceGenerationRequest) -> None:
        missing = []
        if not self.access_key_id:
            missing.append("ALIYUN_ACCESS_KEY_ID")
        if not self.access_key_secret:
            missing.append("ALIYUN_ACCESS_KEY_SECRET")
        if not self.app_key:
            missing.append("ALIYUN_TTS_APP_KEY")
        if not self.region:
            missing.append("ALIYUN_TTS_REGION")
        if missing:
            raise VoiceProviderError(
                "阿里云 TTS 配置缺失：" + ", ".join(missing) + "。本次未发送请求。"
            )
        if self.region not in REGION_ENDPOINTS:
            raise VoiceProviderError(
                "ALIYUN_TTS_REGION 当前仅支持 cn-shanghai、cn-beijing、"
                "cn-shenzhen。本次未发送请求。"
            )
        if self.sample_rate not in {8000, 16000}:
            raise VoiceProviderError(
                "阿里云 NLS REST TTS 的 sample_rate 仅支持 8000 或 16000。"
                "本次未发送请求。"
            )
        if self._uses_default_token_fetcher and importlib.util.find_spec(
            "aliyunsdkcore"
        ) is None:
            raise VoiceProviderError(
                "缺少 aliyun-python-sdk-core，无法安全获取 NLS Token。"
                "本次未发送请求。"
            )
        if not request.script.strip():
            raise VoiceProviderError("Voice text 不能为空。本次未发送请求。")
        if len(request.script) > 300:
            raise VoiceProviderError(
                "阿里云 NLS REST 短文本合成最多支持 300 个字符。本次未发送请求。"
            )
        if not request.voice.strip():
            raise VoiceProviderError("Voice name 不能为空。本次未发送请求。")
        super().preflight(request)

    def generate_voice(
        self, request: VoiceGenerationRequest
    ) -> VoiceGenerationResult:
        self.preflight(request)
        try:
            token = str(
                self._token_fetcher(self.access_key_id, self.access_key_secret)
            ).strip()
        except VoiceProviderError:
            raise
        except Exception as exc:
            raise VoiceProviderError(
                "获取阿里云 NLS Token 失败；凭据内容未写入错误信息。"
            ) from exc
        if not token:
            raise VoiceProviderError("阿里云 NLS Token 为空，本次未发送 TTS 请求。")

        payload = {
            "appkey": self.app_key,
            "token": token,
            "text": request.script,
            "format": "wav",
            "sample_rate": self.sample_rate,
            "voice": request.voice,
        }
        for name in ("volume", "speech_rate", "pitch_rate"):
            if name in request.settings:
                payload[name] = request.settings[name]
        try:
            response = self._http_post(
                self.endpoint,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise VoiceProviderError("阿里云 TTS 网络请求失败。") from exc

        body = bytes(getattr(response, "content", b"") or b"")
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == 200 and self._is_wav(body):
            headers = getattr(response, "headers", {}) or {}
            task_id = (
                headers.get("task_id")
                or headers.get("x-nls-task-id")
                or headers.get("X-NLS-Task-Id")
            )
            return VoiceGenerationResult(
                audio_bytes=body,
                provider_task_id=str(task_id) if task_id else None,
                metadata={"region": self.region, "sample_rate": self.sample_rate},
            )
        raise self._response_error(response, status_code, body)

    @staticmethod
    def _is_wav(payload: bytes) -> bool:
        return len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"

    @staticmethod
    def _response_error(response: Any, status_code: int, body: bytes) -> VoiceProviderError:
        data: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            try:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    data = parsed
            except (ValueError, json.JSONDecodeError):
                data = {}
        code = data.get("error_code") or data.get("code") or status_code
        message = data.get("error_message") or data.get("message") or "未知错误"
        request_id = data.get("request_id") or data.get("RequestId")
        details = f"阿里云 TTS 请求失败：code={code} message={message}"
        if request_id:
            details += f" request_id={request_id}"
        return VoiceProviderError(details)

    @staticmethod
    def _fetch_token_with_sdk(access_key_id: str, access_key_secret: str) -> str:
        try:
            from aliyunsdkcore.client import AcsClient
            from aliyunsdkcore.request import CommonRequest
        except ImportError as exc:
            raise VoiceProviderError(
                "缺少 aliyun-python-sdk-core，无法获取阿里云 NLS Token。"
            ) from exc
        client = AcsClient(access_key_id, access_key_secret, TOKEN_REGION)
        request = CommonRequest()
        request.set_method("POST")
        request.set_domain(TOKEN_DOMAIN)
        request.set_version(TOKEN_API_VERSION)
        request.set_action_name("CreateToken")
        try:
            response = client.do_action_with_exception(request)
            payload = json.loads(response)
            return str((payload.get("Token") or {}).get("Id") or "")
        except Exception as exc:
            raise VoiceProviderError(
                "阿里云 NLS Token 获取失败；请检查 RAM 权限与 AccessKey。"
            ) from exc
