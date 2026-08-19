"""Provider-neutral orchestration for asynchronous video generation."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from project_manager import ProjectPaths
from task_logger import TaskLogger
from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_provider import (
    ProviderErrorCode,
    ProviderTask,
    ProviderTaskStatus,
    VideoProviderError,
)
from video_provider_registry import VideoProviderRegistry, create_default_registry


POLL_INTERVAL_SECONDS = 10
MAX_WAIT_SECONDS = 30 * 60
logger = logging.getLogger(__name__)


class ProviderSubmissionUnknownError(RuntimeError):
    """The billable submit may have reached the provider, but no task ID is known."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__("Unable to confirm whether the provider accepted the submission.")
        self.cause = cause


def _submission_is_ambiguous(error: VideoProviderError | OSError) -> bool:
    if isinstance(error, OSError):
        return True
    return error.code in {
        ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
        ProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
    }


def _selection(value: ProviderSelection | Mapping[str, str] | None) -> ProviderSelection | None:
    if value is None or isinstance(value, ProviderSelection):
        return value
    return ProviderSelection(
        str(value["provider"]),
        str(value["model"]),
        str(value.get("selection_mode") or "manual"),
    )


def generate_video(
    provider_credentials: Mapping[str, Any] | str | None,
    prompt: str,
    project: ProjectPaths,
    duration: int = 6,
    resolution: str = "768P",
    output_path: Path | None = None,
    task_logger: TaskLogger | None = None,
    shot_id: int | None = None,
    visual_input: dict[str, Any] | None = None,
    provider_selection: ProviderSelection | Mapping[str, str] | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    resume_task: ProviderTask | None = None,
    on_preflight: Callable[[dict[str, str | None]], None] | None = None,
    on_submitting: Callable[[dict[str, str | None]], None] | None = None,
    on_submitted: Callable[[ProviderTask], None] | None = None,
    on_task_updated: Callable[[ProviderTask], None] | None = None,
    on_downloading: Callable[[ProviderTask], None] | None = None,
    on_downloaded: Callable[[Path], None] | None = None,
) -> Path:
    """Generate one video through the selected adapter without provider API knowledge."""
    if output_path is None:
        raise ValueError("必须提供镜头的 output_path。")
    output_path = project.ensure_within_project(output_path)
    request = VideoGenerationRequest(
        shot_id=shot_id,
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        visual_input=visual_input or {"mode": "none", "source": None, "assets": []},
        project=project,
        provider_selection=_selection(provider_selection),
    )
    registry = provider_registry or create_default_registry(provider_credentials)
    route = registry.preflight(request, resume_task)
    adapter = route.adapter
    generation_mode = adapter.generation_mode(request.required_capability)
    provider_fields = {
        "model": adapter.model_name,
        "generation_mode": generation_mode,
        "provider_api_version": adapter.api_version,
        "selection_mode": route.selection_mode,
        "credential_env_name": route.credential_env_name,
        "visual_input_mode": request.required_capability,
        "reference_asset_ids": [
            str(item.get("asset_id"))
            for item in request.visual_input.get("assets", [])
            if item.get("asset_id")
        ],
        "shot_id": shot_id,
    }
    if task_logger:
        task_logger.event(
            "VIDEO_PROVIDER_SELECTED",
            provider=adapter.provider_name,
            **provider_fields,
        )
    if on_preflight:
        on_preflight(route.metadata(request.required_capability))

    task = resume_task
    try:
        if task is None:
            if task_logger:
                task_logger.api("VIDEO_PROVIDER_SUBMIT", adapter.provider_name, **provider_fields)
            if on_submitting:
                on_submitting(route.metadata(request.required_capability))
            try:
                task = adapter.submit(request, task_logger).evolve(
                    selection_mode=route.selection_mode,
                    credential_env_name=route.credential_env_name,
                )
            except (VideoProviderError, OSError) as exc:
                if _submission_is_ambiguous(exc):
                    raise ProviderSubmissionUnknownError(exc) from exc
                raise
            if on_submitted:
                on_submitted(task)
        else:
            task = task.evolve(
                provider=adapter.provider_name,
                model=adapter.model_name,
                api_version=adapter.api_version,
                generation_mode=generation_mode,
                selection_mode=task.selection_mode or route.selection_mode,
                credential_env_name=(
                    task.credential_env_name or route.credential_env_name
                ),
            )
            if task_logger:
                task_logger.api(
                    "VIDEO_PROVIDER_RESUMED",
                    adapter.provider_name,
                    provider_task_id=task.provider_task_id,
                    **provider_fields,
                )

        started_at = time.monotonic()
        last_status: ProviderTaskStatus | None = None
        while task.status != ProviderTaskStatus.COMPLETED:
            if time.monotonic() - started_at >= MAX_WAIT_SECONDS:
                raise VideoProviderError(
                    ProviderErrorCode.PROVIDER_TEMPORARY_ERROR,
                    f"视频任务在 {MAX_WAIT_SECONDS // 60} 分钟内未完成。",
                    provider=adapter.provider_name,
                    model=adapter.model_name,
                    retryable=True,
                )
            task = adapter.poll(task, task_logger)
            if task.status != last_status:
                logger.info("Provider task status: %s", task.status.value)
                if task_logger:
                    task_logger.api(
                        "VIDEO_PROVIDER_POLL",
                        adapter.provider_name,
                        provider_task_id=task.provider_task_id,
                        provider_status=task.status.value,
                        raw_status=task.raw_status,
                        **provider_fields,
                    )
                last_status = task.status
            if on_task_updated:
                on_task_updated(task)
            if task.status in {ProviderTaskStatus.FAILED, ProviderTaskStatus.CANCELLED}:
                raise VideoProviderError(
                    ProviderErrorCode.TASK_FAILED,
                    f"视频任务状态为 {task.status.value}。",
                    provider=adapter.provider_name,
                    model=adapter.model_name,
                )
            if task.status != ProviderTaskStatus.COMPLETED:
                time.sleep(POLL_INTERVAL_SECONDS)

        if task_logger:
            task_logger.api(
                "VIDEO_PROVIDER_COMPLETED",
                adapter.provider_name,
                provider_task_id=task.provider_task_id,
                **provider_fields,
            )
            task_logger.api(
                "VIDEO_PROVIDER_DOWNLOAD",
                adapter.provider_name,
                provider_task_id=task.provider_task_id,
                output_path=output_path,
                **provider_fields,
            )
        if on_downloading:
            on_downloading(task)
        result = adapter.download(task, output_path, request, task_logger)
        if on_downloaded:
            on_downloaded(result.output_path)
        logger.info("视频生成完成\n保存位置：\n%s", result.output_path)
        return result.output_path
    except ProviderSubmissionUnknownError:
        raise
    except (VideoProviderError, OSError) as exc:
        if task_logger:
            fields = dict(provider_fields)
            if isinstance(exc, VideoProviderError):
                fields.update(
                    error_code=exc.code.value,
                    retryable=exc.retryable,
                    http_status=exc.http_status,
                    provider_error_message=exc.provider_message,
                    request_id=exc.request_id,
                )
            task_logger.api(
                "VIDEO_PROVIDER_FAILED",
                adapter.provider_name,
                error=exc,
                provider_task_id=(task.provider_task_id if task else None),
                **fields,
            )
            task_logger.error(exc, stage=f"video_shot_{shot_id or 'unknown'}")
        raise
