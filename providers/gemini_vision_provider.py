"""Gemini image-understanding adapter behind the generic VisionProvider contract."""

from __future__ import annotations

import base64
import json
import re

import requests
from pydantic import ValidationError

from vision_provider import (
    VisualAnalysis,
    VisionAnalysisRequest,
    VisionProvider,
    VisionProviderCapabilities,
    VisionProviderError,
)


REQUEST_TIMEOUT_SECONDS = 120


class GeminiVisionProvider(VisionProvider):
    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        api_version: str,
        credential_env_name: str,
        supported_image_formats: frozenset[str],
    ) -> None:
        self.api_key = api_key
        self.credential_value = api_key
        self.model_name = model_name
        self.api_version = api_version
        self.credential_env_name = credential_env_name
        self.capabilities = VisionProviderCapabilities(supported_image_formats)

    @property
    def endpoint(self) -> str:
        return (
            f"https://generativelanguage.googleapis.com/{self.api_version}/models/"
            f"{self.model_name}:generateContent"
        )

    @staticmethod
    def _mime_type(image_format: str) -> str:
        normalized = str(image_format).strip().lower()
        return "image/jpeg" if normalized in {"jpg", "jpeg"} else f"image/{normalized}"

    @staticmethod
    def _response_text(payload: dict) -> str:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "\n".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            ).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionProviderError("Gemini Vision 响应缺少可用文本内容。") from exc
        if not text:
            raise VisionProviderError("Gemini Vision 返回了空内容。")
        return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()

    def analyze_image(self, request: VisionAnalysisRequest) -> VisualAnalysis:
        self.preflight(request)
        prompt = (
            "你是品牌视觉分析师。分析参考图片中的可见事实，并结合产品基础信息和用户备注，"
            "输出严格 JSON。不要虚构图片中不可见的品牌、文字或材质。\n"
            f"产品名称：{request.product_name}\n"
            f"产品介绍：{request.product_description}\n"
            f"用户备注：{request.user_notes or '无'}\n"
            "必须输出字段：product_identity, brand_style, visual_features, materials, "
            "colors, composition, must_keep_elements, avoid_elements。"
        )
        encoded = base64.b64encode(request.image_path.read_bytes()).decode("ascii")
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": self._mime_type(request.image_format),
                                "data": encoded,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": VisualAnalysis.model_json_schema(),
            },
        }
        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            response_payload = response.json()
        except requests.Timeout as exc:
            raise VisionProviderError("Gemini Vision 请求超时，请稍后重试。") from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            provider_message = ""
            if exc.response is not None:
                try:
                    error_payload = exc.response.json()
                    provider_message = str(
                        (error_payload.get("error") or {}).get("message") or ""
                    ).strip()
                except (ValueError, AttributeError):
                    provider_message = ""
            suffix = f"：{provider_message}" if provider_message else ""
            raise VisionProviderError(
                f"Gemini Vision API 返回 HTTP {status}{suffix}"
            ) from exc
        except requests.RequestException as exc:
            raise VisionProviderError(f"Gemini Vision 网络请求失败：{exc}") from exc
        except ValueError as exc:
            raise VisionProviderError("Gemini Vision 返回了无效 JSON 响应。") from exc

        try:
            structured = json.loads(self._response_text(response_payload))
            return VisualAnalysis.model_validate(structured)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise VisionProviderError(f"Gemini Vision 结构化结果无效：{exc}") from exc
