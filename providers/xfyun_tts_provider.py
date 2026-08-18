"""iFLYTEK online TTS WebSocket v2 adapter.

The provider returns a project-neutral WAV payload.  iFLYTEK streams raw PCM
audio for ``aue=raw``; this adapter wraps those bytes in a RIFF/WAVE container
locally so the existing Voice Asset pipeline does not need provider-specific
logic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import io
import json
import re
import wave
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderCapabilities,
    VoiceProviderError,
)


XFYUN_HOST = "tts-api.xfyun.cn"
XFYUN_PATH = "/v2/tts"
XFYUN_ENDPOINT = f"wss://{XFYUN_HOST}{XFYUN_PATH}"
VOICE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class XfyunTTSProvider(VoiceProvider):
    """Streaming online TTS using APPID/APIKey/APISecret authentication."""

    provider_name = "xfyun_tts"
    api_version = "websocket-v2"
    capabilities = VoiceProviderCapabilities(
        supported_languages=frozenset({"zh-CN"}),
        supported_formats=frozenset({"wav"}),
    )

    def __init__(
        self,
        *,
        app_id: str,
        api_key: str,
        api_secret: str,
        model: str = "online-tts-v2",
        sample_rate: int = 16000,
        timeout_seconds: float = 60.0,
        allowed_voices: tuple[str, ...] | list[str] | None = None,
        websocket_factory: Callable[..., Any] | None = None,
        date_factory: Callable[[], datetime | str] | None = None,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.api_key = str(api_key or "").strip()
        self.api_secret = str(api_secret or "").strip()
        self.model_name = str(model or "online-tts-v2").strip()
        self.sample_rate = int(sample_rate)
        self.timeout_seconds = float(timeout_seconds)
        self.allowed_voices = tuple(
            str(item).strip()
            for item in (allowed_voices or ())
            if str(item).strip()
        )
        self._uses_default_websocket_factory = websocket_factory is None
        self._websocket_factory = (
            websocket_factory or self._default_websocket_factory
        )
        self._date_factory = date_factory or (
            lambda: datetime.now(timezone.utc)
        )

    @classmethod
    def from_env(
        cls,
        settings: Mapping[str, Any] | None = None,
        *,
        environ: Mapping[str, str],
        websocket_factory: Callable[..., Any] | None = None,
        date_factory: Callable[[], datetime | str] | None = None,
    ) -> "XfyunTTSProvider":
        config = dict(settings or {})
        voices = config.get("allowed_voices")
        if not isinstance(voices, (list, tuple)):
            voices = ()
        return cls(
            app_id=environ.get("XFYUN_APP_ID", ""),
            api_key=environ.get("XFYUN_API_KEY", ""),
            api_secret=environ.get("XFYUN_API_SECRET", ""),
            model=str(config.get("model") or "online-tts-v2"),
            sample_rate=int(config.get("sample_rate") or 16000),
            timeout_seconds=float(config.get("timeout_seconds") or 60.0),
            allowed_voices=voices,
            websocket_factory=websocket_factory,
            date_factory=date_factory,
        )

    def supports(self, request: VoiceGenerationRequest) -> bool:
        return super().supports(request)

    def get_metadata(self) -> dict[str, Any]:
        metadata = super().get_metadata()
        metadata.update(
            {
                "endpoint": XFYUN_ENDPOINT,
                "sample_rate": self.sample_rate,
                "audio_transport_format": "raw-pcm",
                "max_text_bytes": 7999,
                "credential_env_names": [
                    "XFYUN_APP_ID",
                    "XFYUN_API_KEY",
                    "XFYUN_API_SECRET",
                ],
            }
        )
        return metadata

    def preflight(self, request: VoiceGenerationRequest) -> None:
        missing = []
        if not self.app_id:
            missing.append("XFYUN_APP_ID")
        if not self.api_key:
            missing.append("XFYUN_API_KEY")
        if not self.api_secret:
            missing.append("XFYUN_API_SECRET")
        if missing:
            raise VoiceProviderError(
                "讯飞 TTS 配置缺失：" + ", ".join(missing) + "。本次未发送请求。"
            )
        if self.sample_rate not in {8000, 16000}:
            raise VoiceProviderError(
                "讯飞在线语音合成 sample_rate 仅支持 8000 或 16000。"
                "本次未发送请求。"
            )
        text_bytes = request.script.encode("utf-8")
        if not text_bytes:
            raise VoiceProviderError("Voice text 不能为空。本次未发送请求。")
        if len(text_bytes) >= 8000:
            raise VoiceProviderError(
                "讯飞在线语音合成单次文本必须小于 8000 字节。"
                "本次未发送请求。"
            )
        voice = request.voice.strip()
        if not VOICE_CODE_PATTERN.fullmatch(voice):
            raise VoiceProviderError(
                "讯飞 Voice 必须填写控制台显示的发音人参数值，"
                "仅支持字母、数字、下划线或连字符。本次未发送请求。"
            )
        if self.allowed_voices and voice not in self.allowed_voices:
            raise VoiceProviderError(
                f"讯飞 Voice {voice} 不在当前配置允许的发音人列表中。"
                "本次未发送请求。"
            )
        if self._uses_default_websocket_factory and importlib.util.find_spec(
            "websocket"
        ) is None:
            raise VoiceProviderError(
                "缺少 websocket-client，无法连接讯飞 TTS。"
                "请安装 requirements.txt 后重试；本次未发送请求。"
            )
        super().preflight(request)

    def build_authenticated_url(self, date_value: datetime | str | None = None) -> str:
        """Build the signed URL without logging or persisting it."""
        current = date_value if date_value is not None else self._date_factory()
        date_text = self._format_date(current)
        signature_origin = (
            f"host: {XFYUN_HOST}\n"
            f"date: {date_text}\n"
            f"GET {XFYUN_PATH} HTTP/1.1"
        )
        digest = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature = base64.b64encode(digest).decode("ascii")
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(
            authorization_origin.encode("utf-8")
        ).decode("ascii")
        return f"{XFYUN_ENDPOINT}?{urlencode({'authorization': authorization, 'date': date_text, 'host': XFYUN_HOST})}"

    def build_request_payload(self, request: VoiceGenerationRequest) -> dict[str, Any]:
        business: dict[str, Any] = {
            "aue": "raw",
            "auf": f"audio/L16;rate={self.sample_rate}",
            "vcn": request.voice,
            "tte": "UTF8",
        }
        for name in ("speed", "volume", "pitch", "bgs", "reg", "rdn"):
            if name in request.settings:
                business[name] = request.settings[name]
        return {
            "common": {"app_id": self.app_id},
            "business": business,
            "data": {
                "status": 2,
                "text": base64.b64encode(
                    request.script.encode("utf-8")
                ).decode("ascii"),
            },
        }

    def generate_voice(
        self, request: VoiceGenerationRequest
    ) -> VoiceGenerationResult:
        self.preflight(request)
        connection = None
        pcm_chunks: list[bytes] = []
        session_id: str | None = None
        completed = False
        try:
            signed_url = self.build_authenticated_url()
            connection = self._websocket_factory(
                signed_url,
                timeout=self.timeout_seconds,
            )
            connection.send(
                json.dumps(self.build_request_payload(request), ensure_ascii=False)
            )
            while True:
                raw_message = connection.recv()
                if raw_message in (None, "", b""):
                    raise VoiceProviderError(
                        "讯飞 TTS 连接在返回完成状态前结束。"
                    )
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                try:
                    response = json.loads(raw_message)
                except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    raise VoiceProviderError(
                        "讯飞 TTS 返回了无法解析的 WebSocket 消息。"
                    ) from None
                if not isinstance(response, dict):
                    raise VoiceProviderError("讯飞 TTS 返回结构无效。")
                session_id = str(response.get("sid") or session_id or "") or None
                code = int(response.get("code", 0) or 0)
                if code != 0:
                    message = self._redact(str(response.get("message") or "未知错误"))
                    details = f"讯飞 TTS 请求失败：code={code} message={message}"
                    if session_id:
                        details += f" sid={session_id}"
                    raise VoiceProviderError(details)
                data = response.get("data")
                if not isinstance(data, dict):
                    raise VoiceProviderError("讯飞 TTS 响应缺少 data。")
                audio = data.get("audio")
                if audio:
                    try:
                        pcm_chunks.append(base64.b64decode(str(audio), validate=True))
                    except (ValueError, TypeError):
                        raise VoiceProviderError(
                            "讯飞 TTS 返回了无效的音频分片。"
                        ) from None
                if int(data.get("status", 0) or 0) == 2:
                    completed = True
                    break
        except VoiceProviderError:
            raise
        except Exception:
            raise VoiceProviderError(
                "讯飞 TTS WebSocket 请求失败；凭据与签名未写入错误信息。"
            ) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        pcm = b"".join(pcm_chunks)
        if not completed or not pcm:
            raise VoiceProviderError("讯飞 TTS 未返回有效音频。")
        wav_bytes = self._pcm_to_wav(pcm)
        duration = round(len(pcm) / (self.sample_rate * 2), 6)
        return VoiceGenerationResult(
            audio_bytes=wav_bytes,
            duration_seconds=duration,
            provider_task_id=session_id,
            metadata={
                "sample_rate": self.sample_rate,
                "source_audio_format": "raw-pcm",
            },
        )

    def _redact(self, value: str) -> str:
        safe = value
        for secret in (self.app_id, self.api_key, self.api_secret):
            if secret:
                safe = safe.replace(secret, "***")
        return safe

    def _pcm_to_wav(self, pcm: bytes) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(self.sample_rate)
            stream.writeframes(pcm)
        return buffer.getvalue()

    @staticmethod
    def _format_date(value: datetime | str) -> str:
        if isinstance(value, str):
            if not value.strip():
                raise VoiceProviderError("讯飞鉴权时间不能为空。")
            return value.strip()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return format_datetime(value.astimezone(timezone.utc), usegmt=True)

    @staticmethod
    def _default_websocket_factory(url: str, *, timeout: float) -> Any:
        try:
            import websocket
        except ImportError:
            raise VoiceProviderError(
                "缺少 websocket-client，无法连接讯飞 TTS。"
            ) from None
        return websocket.create_connection(url, timeout=timeout)
