"""MiniMax Hailuo 2.3 v1 adapter."""

from __future__ import annotations

from pathlib import Path

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


class MiniMaxHailuoProvider(VideoProvider):
    provider_name = "minimax"
    model_name = "MiniMax-Hailuo-2.3"
    api_version = "v1"
    capabilities = ProviderCapabilities(
        frozenset({"none", "first_frame"}),
        supported_resolutions=frozenset({"768P"}),
        supported_durations=frozenset({6, 10}),
    )
    generation_mode_by_visual_mode = {
        "none": "text_to_video",
        "first_frame": "first_frame",
    }

    def __init__(
        self, api_key: str, credential_env_name: str = "MINIMAX_API_KEY"
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
        if request.duration not in {6, 10} or request.resolution != "768P":
            raise VideoProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "Hailuo 2.3 当前支持 768P、6 秒或 10 秒。",
                provider=self.provider_name,
                model=self.model_name,
            )
        payload = {
            "model": self.model_name,
            "prompt": request.prompt,
            "duration": request.duration,
            "resolution": request.resolution,
        }
        if mode == "first_frame":
            payload["first_frame_image"] = visual_asset_data_urls(request)[0][1]
        return payload

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
            f"{api_base_url(self.api_version)}/query/video_generation",
            headers(self.api_key, self.model_name),
            model=self.model_name,
            params={"task_id": task.provider_task_id},
        )
        check_base_response(data, self.model_name, "查询视频任务")
        raw = str(data.get("status") or "Unknown")
        status = {
            "Preparing": ProviderTaskStatus.QUEUED,
            "Queueing": ProviderTaskStatus.QUEUED,
            "Processing": ProviderTaskStatus.GENERATING,
            "Success": ProviderTaskStatus.COMPLETED,
            "Fail": ProviderTaskStatus.FAILED,
        }.get(raw, ProviderTaskStatus.GENERATING)
        if status == ProviderTaskStatus.FAILED:
            raise VideoProviderError(
                ProviderErrorCode.TASK_FAILED,
                f"视频任务失败：{data.get('error_message') or '未知原因'}",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        file_id = data.get("file_id") if status == ProviderTaskStatus.COMPLETED else None
        if status == ProviderTaskStatus.COMPLETED and not file_id:
            raise VideoProviderError(
                ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                "任务成功但未返回 file_id。",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        return task.evolve(
            status=status,
            raw_status=raw,
            provider_file_id=str(file_id) if file_id else task.provider_file_id,
        )

    def download(self, task, output_path, request, task_logger=None) -> DownloadResult:
        if not task.provider_file_id:
            raise VideoProviderError(
                ProviderErrorCode.DOWNLOAD_FAILED,
                "下载前缺少 provider_file_id。",
                provider=self.provider_name,
                model=self.model_name,
            )
        data = request_json(
            "GET",
            f"{api_base_url(self.api_version)}/files/retrieve",
            headers(self.api_key, self.model_name),
            model=self.model_name,
            params={"file_id": task.provider_file_id},
        )
        check_base_response(data, self.model_name, "获取视频文件")
        url = (data.get("file") or {}).get("download_url")
        if not url:
            raise VideoProviderError(
                ProviderErrorCode.DOWNLOAD_FAILED,
                "Provider 未返回下载地址。",
                provider=self.provider_name,
                model=self.model_name,
                raw_error=data,
            )
        return download_from_url(str(url), output_path, request, model=self.model_name)
