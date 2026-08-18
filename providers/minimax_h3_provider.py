"""MiniMax H3 v2 reference-generation adapter."""

from __future__ import annotations

from task_logger import TaskLogger
from video_generation_request import VideoGenerationRequest
from video_provider import (
    DownloadResult,
    ProviderCapabilities,
    ProviderErrorCode,
    ProviderTask,
    ProviderTaskStatus,
    VideoProvider,
    VideoProviderError,
)

from .minimax_common import (
    api_base_url,
    check_base_response,
    download_from_url,
    headers,
    request_json,
    validate_visual_assets,
    visual_asset_data_urls,
)


class MiniMaxH3Provider(VideoProvider):
    provider_name = "minimax"
    model_name = "MiniMax-H3"
    api_version = "v2"
    capabilities = ProviderCapabilities(
        frozenset({"none", "first_frame", "reference_asset"}),
        supported_resolutions=frozenset({"768P", "2K"}),
        min_duration=4,
        max_duration=15,
    )
    generation_mode_by_visual_mode = {
        "none": "text_to_video",
        "first_frame": "first_frame",
        "reference_asset": "reference_generation",
    }

    def __init__(
        self, api_key: str, credential_env_name: str = "MINIMAX_H3_API_KEY"
    ) -> None:
        self.api_key = api_key
        self.credential_value = api_key
        self.credential_env_name = credential_env_name

    def preflight(self, request: VideoGenerationRequest) -> None:
        super().preflight(request)
        validate_visual_assets(request)

    def build_payload(self, request: VideoGenerationRequest) -> dict:
        mode = request.required_capability
        self.generation_mode(mode)
        if (
            not isinstance(request.duration, int)
            or not 4 <= request.duration <= 15
            or request.resolution not in {"768P", "2K"}
        ):
            raise VideoProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "H3 当前支持 4-15 秒、768P 或 2K。",
                provider=self.provider_name,
                model=self.model_name,
            )
        content: list[dict] = [{"type": "text", "text": request.prompt}]
        if mode != "none":
            role = "reference_image" if mode == "reference_asset" else "first_frame"
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                    "role": role,
                }
                for _asset, data_url in visual_asset_data_urls(request)
            )
        return {
            "model": self.model_name,
            "content": content,
            "duration": request.duration,
            "resolution": request.resolution,
        }

    def submit(self, request, task_logger=None) -> ProviderTask:
        data = request_json(
            "POST",
            f"{api_base_url(self.api_version)}/video_generation",
            headers(self.api_key, self.model_name),
            model=self.model_name,
            json=self.build_payload(request),
        )
        check_base_response(data, self.model_name, "创建视频任务")
        task_id = data.get("task_id")
        if not task_id:
            raise VideoProviderError(
                ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                "Provider 未返回 task_id。",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        return ProviderTask(
            self.provider_name,
            self.model_name,
            self.api_version,
            self.generation_mode(request.required_capability),
            str(task_id),
        )

    def poll(self, task, task_logger=None) -> ProviderTask:
        data = request_json(
            "GET",
            f"{api_base_url(self.api_version)}/query/video_generation/{task.provider_task_id}",
            headers(self.api_key, self.model_name),
            model=self.model_name,
        )
        check_base_response(data, self.model_name, "查询视频任务")
        provider_task = data.get("task") or {}
        raw = str(provider_task.get("status") or data.get("status") or "unknown").lower()
        status = {
            "queued": ProviderTaskStatus.QUEUED,
            "running": ProviderTaskStatus.GENERATING,
            "succeeded": ProviderTaskStatus.COMPLETED,
            "failed": ProviderTaskStatus.FAILED,
            "cancelled": ProviderTaskStatus.CANCELLED,
        }.get(raw, ProviderTaskStatus.GENERATING)
        if status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELLED}:
            raise VideoProviderError(
                ProviderErrorCode.TASK_FAILED,
                f"视频任务状态为 {raw}。",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        content = provider_task.get("content") or {}
        url = content.get("url") if isinstance(content, dict) else None
        if status == ProviderTaskStatus.COMPLETED and not url:
            raise VideoProviderError(
                ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                "任务成功但未返回输出地址。",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        return task.evolve(
            status=status,
            raw_status=raw,
            output_locator=str(url) if url else task.output_locator,
        )

    def download(self, task, output_path, request, task_logger=None) -> DownloadResult:
        if not task.output_locator:
            raise VideoProviderError(
                ProviderErrorCode.DOWNLOAD_FAILED,
                "下载前缺少 output locator。",
                provider=self.provider_name,
                model=self.model_name,
            )
        return download_from_url(
            task.output_locator, output_path, request, model=self.model_name
        )
