"""Shared transport helpers for the built-in MiniMax adapters."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import requests

from reference_assets import ReferenceAssetManager, inspect_image
from video_generation_request import VideoGenerationRequest
from video_provider import (
    DownloadResult,
    ProviderErrorCode,
    VideoProviderError,
)


REQUEST_TIMEOUT_SECONDS = 60


def api_base_url(version: str) -> str:
    configured = os.getenv(
        "MINIMAX_API_BASE_URL", "https://api.minimaxi.com/v1"
    ).rstrip("/")
    origin = configured
    for suffix in ("/v1", "/v2"):
        if origin.endswith(suffix):
            origin = origin[: -len(suffix)]
            break
    return f"{origin}/{version}"


def headers(api_key: str, model: str) -> dict[str, str]:
    if not api_key.strip():
        raise VideoProviderError(
            ProviderErrorCode.AUTH_ERROR,
            "视频 Provider API Key 不能为空。",
            provider="minimax",
            model=model,
        )
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _http_error_code(status: int) -> tuple[ProviderErrorCode, bool]:
    if status in {401, 403}:
        return ProviderErrorCode.AUTH_ERROR, False
    if status == 402:
        return ProviderErrorCode.QUOTA_ERROR, False
    if status == 429:
        return ProviderErrorCode.RATE_LIMIT, True
    if status in {400, 404, 409, 422}:
        return ProviderErrorCode.INVALID_REQUEST, False
    if status >= 500:
        return ProviderErrorCode.PROVIDER_TEMPORARY_ERROR, True
    return ProviderErrorCode.UNKNOWN_PROVIDER_ERROR, False


def _safe_http_error_details(response: requests.Response | None) -> tuple[Any, str | None, str | None]:
    """Extract only provider-owned error fields; never echo request headers/payloads."""
    if response is None:
        return None, None, None
    try:
        payload: Any = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:2000], text[:1000] or None, None
    if not isinstance(payload, dict):
        return payload, None, None
    error = payload.get("error")
    provider_message: str | None = None
    if isinstance(error, dict):
        provider_message = str(error.get("message") or "").strip() or None
    if provider_message is None:
        provider_message = str(
            payload.get("message") or payload.get("status_msg") or ""
        ).strip() or None
    request_id = str(payload.get("request_id") or "").strip() or None
    return payload, provider_message, request_id


def request_json(
    method: str,
    url: str,
    request_headers: dict[str, str],
    *,
    model: str,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            headers=request_headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout as exc:
        raise VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            "视频 Provider 请求超时。",
            provider="minimax",
            model=model,
            retryable=True,
            raw_error=repr(exc),
        ) from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        code, retryable = _http_error_code(status)
        raw, provider_message, request_id = _safe_http_error_details(exc.response)
        raise VideoProviderError(
            code,
            f"MiniMax 请求失败（HTTP {status or 'unknown'}）。",
            provider="minimax",
            model=model,
            retryable=retryable,
            raw_error=raw,
            provider_message=provider_message,
            request_id=request_id,
            http_status=status or None,
        ) from exc
    except requests.RequestException as exc:
        raise VideoProviderError(
            ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
            f"视频 Provider 网络请求失败：{exc}",
            provider="minimax",
            model=model,
            retryable=True,
            raw_error=repr(exc),
        ) from exc
    except ValueError as exc:
        raise VideoProviderError(
            ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
            "视频 Provider 返回了无效 JSON。",
            provider="minimax",
            model=model,
            raw_error=repr(exc),
        ) from exc
    if not isinstance(data, dict):
        raise VideoProviderError(
            ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
            "视频 Provider JSON 响应不是对象。",
            provider="minimax",
            model=model,
            raw_error=data,
        )
    return data


def check_base_response(data: dict[str, Any], model: str, action: str) -> None:
    base = data.get("base_resp") or {}
    status_code = int(base.get("status_code") or 0)
    if status_code == 0:
        return
    message = str(base.get("status_msg") or "Unknown provider error")
    if status_code in {1008, 1024}:
        code, retryable = ProviderErrorCode.QUOTA_ERROR, False
    elif status_code in {1002, 1004}:
        code, retryable = ProviderErrorCode.AUTH_ERROR, False
    elif status_code in {1005, 1039}:
        code, retryable = ProviderErrorCode.RATE_LIMIT, True
    elif status_code in {1026, 1027}:
        code, retryable = ProviderErrorCode.TASK_FAILED, False
    else:
        code, retryable = ProviderErrorCode.INVALID_REQUEST, False
    raise VideoProviderError(
        code,
        f"{action}失败（{status_code}）：{message}",
        provider="minimax",
        model=model,
        retryable=retryable,
        raw_error=data,
        provider_message=message,
        request_id=str(data.get("request_id") or "").strip() or None,
    )


def visual_asset_data_urls(
    request: VideoGenerationRequest,
) -> list[tuple[dict[str, Any], str]]:
    manager = ReferenceAssetManager(request.project)
    visual = manager.validate_visual_input(request.visual_input)
    result: list[tuple[dict[str, Any], str]] = []
    for asset in visual["assets"]:
        path = manager.asset_path(asset["asset_id"])
        image_type, _width, _height = inspect_image(path)
        mime = "image/jpeg" if image_type == "jpeg" else f"image/{image_type}"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        result.append((asset, f"data:{mime};base64,{encoded}"))
    return result


def validate_visual_assets(request: VideoGenerationRequest) -> list[dict[str, Any]]:
    """Validate referenced project assets without encoding or sending image bytes."""
    if request.required_capability == "none":
        return []
    manager = ReferenceAssetManager(request.project)
    visual = manager.validate_visual_input(request.visual_input)
    inspected: list[dict[str, Any]] = []
    for asset in visual["assets"]:
        path = manager.asset_path(asset["asset_id"])
        image_type, width, height = inspect_image(path)
        inspected.append(
            {
                "asset_id": asset["asset_id"],
                "sha256": asset.get("sha256"),
                "format": image_type,
                "width": width,
                "height": height,
                "file_size": path.stat().st_size,
            }
        )
    return inspected


def download_from_url(
    url: str,
    output_path: Path,
    request: VideoGenerationRequest,
    *,
    model: str,
) -> DownloadResult:
    target = request.project.ensure_within_project(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=(30, 300)) as response:
            response.raise_for_status()
            with target.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
    except (requests.RequestException, OSError) as exc:
        target.unlink(missing_ok=True)
        raise VideoProviderError(
            ProviderErrorCode.DOWNLOAD_FAILED,
            f"视频下载失败：{exc}",
            provider="minimax",
            model=model,
            retryable=True,
            raw_error=repr(exc),
        ) from exc
    size = target.stat().st_size
    if size <= 0:
        target.unlink(missing_ok=True)
        raise VideoProviderError(
            ProviderErrorCode.DOWNLOAD_FAILED,
            "视频 Provider 下载结果为空文件。",
            provider="minimax",
            model=model,
            retryable=True,
        )
    return DownloadResult(target, size)
