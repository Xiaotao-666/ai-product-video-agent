"""Safe, consistent HTTP error responses for the Web backend."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


error_logger = logging.getLogger("uvicorn.error.web_errors")


class WebErrorDetail(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    correlation_id: str


class WebErrorResponse(BaseModel):
    error: WebErrorDetail


class WebApiError(Exception):
    """An expected API failure whose client representation is explicitly safe."""

    def __init__(
        self,
        *,
        status_code: int,
        error_type: str,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.safe_message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ErrorDefinition:
    status_code: int
    error_type: str
    code: str
    message: str
    retryable: bool = False


ERROR_DEFINITIONS: dict[str, ErrorDefinition] = {
    "ROUTE_NOT_FOUND": ErrorDefinition(
        404, "NOT_FOUND", "ROUTE_NOT_FOUND", "请求的资源不存在。"
    ),
    "HTTP_REQUEST_ERROR": ErrorDefinition(
        400, "HTTP_ERROR", "HTTP_REQUEST_ERROR", "请求无法处理。"
    ),
    "INVALID_REQUEST": ErrorDefinition(
        422, "VALIDATION_ERROR", "INVALID_REQUEST", "请求参数无效。"
    ),
    "UNEXPECTED_ERROR": ErrorDefinition(
        500, "INTERNAL_ERROR", "UNEXPECTED_ERROR", "请求处理失败。"
    ),
    "INVALID_PROJECT_ID": ErrorDefinition(
        422, "PROJECT_ERROR", "INVALID_PROJECT_ID", "项目标识无效。"
    ),
    "PROJECT_NOT_FOUND": ErrorDefinition(
        404, "PROJECT_ERROR", "PROJECT_NOT_FOUND", "项目不存在。"
    ),
    "PROJECT_DATA_CORRUPT": ErrorDefinition(
        422, "PROJECT_ERROR", "PROJECT_DATA_CORRUPT", "项目数据无法读取。"
    ),
    "PROJECT_DATA_UNSUPPORTED": ErrorDefinition(
        422,
        "PROJECT_ERROR",
        "PROJECT_DATA_UNSUPPORTED",
        "项目数据版本暂不支持。",
    ),
    "INVALID_PROJECT_NAME": ErrorDefinition(
        422, "VALIDATION_ERROR", "INVALID_PROJECT_NAME", "项目名称无效。"
    ),
    "INVALID_VIDEO_DURATION": ErrorDefinition(
        422,
        "VALIDATION_ERROR",
        "INVALID_VIDEO_DURATION",
        "视频总时长必须由支持的镜头时长组合构成。",
    ),
    "INVALID_PROJECT_REQUEST": ErrorDefinition(
        422, "VALIDATION_ERROR", "INVALID_PROJECT_REQUEST", "产品需求无效。"
    ),
    "PROJECT_BUSY": ErrorDefinition(
        409,
        "PROJECT_ERROR",
        "PROJECT_BUSY",
        "项目当前正在执行其他操作，请稍后重试。",
        retryable=True,
    ),
    "PAID_CALL_CONFIRMATION_REQUIRED": ErrorDefinition(
        422,
        "GENERATION_ERROR",
        "PAID_CALL_CONFIRMATION_REQUIRED",
        "必须明确确认付费调用后才能生成视频。",
    ),
    "GENERATION_PREFLIGHT_STALE": ErrorDefinition(
        409,
        "GENERATION_ERROR",
        "GENERATION_PREFLIGHT_STALE",
        "生成配置已发生变化，请重新检查配置。",
    ),
    "GENERATION_NOT_RESUMABLE": ErrorDefinition(
        409,
        "GENERATION_ERROR",
        "GENERATION_NOT_RESUMABLE",
        "当前镜头没有可安全继续的生成进度。",
    ),
    "PROJECT_CREATE_FAILED": ErrorDefinition(
        500, "PROJECT_ERROR", "PROJECT_CREATE_FAILED", "项目创建失败。"
    ),
    "ACTION_NOT_ALLOWED": ErrorDefinition(
        409,
        "ACTION_ERROR",
        "ACTION_NOT_ALLOWED",
        "当前项目状态不允许执行此操作。",
    ),
    "PENDING_VERSION_REQUIRES_REVIEW": ErrorDefinition(
        409,
        "ACTION_ERROR",
        "PENDING_VERSION_REQUIRES_REVIEW",
        "请先处理当前待审核新版本，再切换正式历史版本。",
    ),
    "CAPABILITY_UNAVAILABLE": ErrorDefinition(
        503,
        "CAPABILITY_ERROR",
        "CAPABILITY_UNAVAILABLE",
        "创意生成服务尚未配置。",
        retryable=True,
    ),
    "PROMPT_REVISION_DRAFT_NOT_FOUND": ErrorDefinition(
        404,
        "PROMPT_REVISION_ERROR",
        "PROMPT_REVISION_DRAFT_NOT_FOUND",
        "当前镜头没有可查看的AI Prompt修改建议。",
    ),
    "TASK_RUNNER_UNAVAILABLE": ErrorDefinition(
        503,
        "TASK_ERROR",
        "TASK_RUNNER_UNAVAILABLE",
        "任务执行器当前不可用。",
        retryable=True,
    ),
    "INVALID_SHOT_ID": ErrorDefinition(
        422, "SHOT_ERROR", "INVALID_SHOT_ID", "镜头标识无效。"
    ),
    "SHOT_NOT_FOUND": ErrorDefinition(
        404, "SHOT_ERROR", "SHOT_NOT_FOUND", "镜头不存在或已被删除。"
    ),
    "SHOT_DATA_CORRUPT": ErrorDefinition(
        422, "SHOT_ERROR", "SHOT_DATA_CORRUPT", "镜头数据无法读取。"
    ),
    "INVALID_SHOT_VERSION": ErrorDefinition(
        422, "SHOT_ERROR", "INVALID_SHOT_VERSION", "镜头版本无效。"
    ),
    "VIDEO_NOT_FOUND": ErrorDefinition(
        404, "SHOT_ERROR", "VIDEO_NOT_FOUND", "视频文件不可用。"
    ),
    "INVALID_REFERENCE_ASSET_ID": ErrorDefinition(
        422, "REFERENCE_ERROR", "INVALID_REFERENCE_ASSET_ID", "参考素材标识无效。"
    ),
    "REFERENCE_ASSET_NOT_FOUND": ErrorDefinition(
        404, "REFERENCE_ERROR", "REFERENCE_ASSET_NOT_FOUND", "参考素材不存在。"
    ),
    "REFERENCE_ASSET_DATA_CORRUPT": ErrorDefinition(
        422, "REFERENCE_ERROR", "REFERENCE_ASSET_DATA_CORRUPT", "参考素材无法安全读取。"
    ),
    "INVALID_REFERENCE_FILE": ErrorDefinition(
        422, "REFERENCE_ERROR", "INVALID_REFERENCE_FILE", "参考素材文件为空或无效。"
    ),
    "UNSUPPORTED_IMAGE_FORMAT": ErrorDefinition(
        415,
        "REFERENCE_ERROR",
        "UNSUPPORTED_IMAGE_FORMAT",
        "仅支持 JPG、JPEG、PNG 和 WebP 图片。",
    ),
    "REFERENCE_IMAGE_INVALID": ErrorDefinition(
        422, "REFERENCE_ERROR", "REFERENCE_IMAGE_INVALID", "图片内容无法安全读取。"
    ),
    "REFERENCE_FILE_TOO_LARGE": ErrorDefinition(
        413,
        "REFERENCE_ERROR",
        "REFERENCE_FILE_TOO_LARGE",
        "参考素材超过 20MB 大小限制。",
    ),
    "REFERENCE_IMPORT_FAILED": ErrorDefinition(
        500,
        "REFERENCE_ERROR",
        "REFERENCE_IMPORT_FAILED",
        "参考素材导入失败，请稍后重试。",
        retryable=True,
    ),
    "ASSEMBLY_DATA_CORRUPT": ErrorDefinition(
        422, "ASSEMBLY_ERROR", "ASSEMBLY_DATA_CORRUPT", "合片数据无法读取。"
    ),
    "ASSEMBLY_MEDIA_NOT_FOUND": ErrorDefinition(
        404, "ASSEMBLY_ERROR", "ASSEMBLY_MEDIA_NOT_FOUND", "合片视频不可用。"
    ),
    "VOICE_DATA_CORRUPT": ErrorDefinition(
        422, "POST_PRODUCTION_ERROR", "VOICE_DATA_CORRUPT", "配音数据无法读取。"
    ),
    "VOICE_MEDIA_NOT_FOUND": ErrorDefinition(
        404, "POST_PRODUCTION_ERROR", "VOICE_MEDIA_NOT_FOUND", "配音音频不可用。"
    ),
    "SUBTITLE_DATA_CORRUPT": ErrorDefinition(
        422, "POST_PRODUCTION_ERROR", "SUBTITLE_DATA_CORRUPT", "字幕数据无法读取。"
    ),
    "MUSIC_DATA_CORRUPT": ErrorDefinition(
        422, "POST_PRODUCTION_ERROR", "MUSIC_DATA_CORRUPT", "音乐数据无法读取。"
    ),
    "MUSIC_MEDIA_NOT_FOUND": ErrorDefinition(
        404, "POST_PRODUCTION_ERROR", "MUSIC_MEDIA_NOT_FOUND", "音乐音频不可用。"
    ),
    "EXPORT_DATA_CORRUPT": ErrorDefinition(
        422, "EXPORT_ERROR", "EXPORT_DATA_CORRUPT", "最终导出数据无法读取。"
    ),
    "EXPORT_MEDIA_NOT_FOUND": ErrorDefinition(
        404, "EXPORT_ERROR", "EXPORT_MEDIA_NOT_FOUND", "最终成片不可用。"
    ),
    "INVALID_TASK_ID": ErrorDefinition(
        422, "TASK_ERROR", "INVALID_TASK_ID", "任务标识无效。"
    ),
    "TASK_NOT_FOUND": ErrorDefinition(
        404, "TASK_ERROR", "TASK_NOT_FOUND", "任务不存在。"
    ),
    "TASK_DATA_CORRUPT": ErrorDefinition(
        422, "TASK_ERROR", "TASK_DATA_CORRUPT", "任务记录无法读取。"
    ),
}


def registered_api_error(code: str) -> WebApiError:
    definition = ERROR_DEFINITIONS[code]
    return WebApiError(
        status_code=definition.status_code,
        error_type=definition.error_type,
        code=definition.code,
        message=definition.message,
        retryable=definition.retryable,
    )


def request_correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "req_unavailable")


def error_response(
    request: Request,
    *,
    status_code: int,
    error_type: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> JSONResponse:
    payload = WebErrorResponse(
        error=WebErrorDetail(
            type=error_type,
            code=code,
            message=message,
            retryable=retryable,
            correlation_id=request_correlation_id(request),
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def registered_error_response(
    request: Request,
    code: str,
    *,
    status_code: int | None = None,
) -> JSONResponse:
    definition = ERROR_DEFINITIONS[code]
    return error_response(
        request,
        status_code=status_code or definition.status_code,
        error_type=definition.error_type,
        code=definition.code,
        message=definition.message,
        retryable=definition.retryable,
    )


def unexpected_error_response(request: Request) -> JSONResponse:
    return registered_error_response(request, "UNEXPECTED_ERROR")


async def http_exception_handler(
    request: Request,
    exception: StarletteHTTPException,
) -> JSONResponse:
    if exception.status_code == 404:
        return registered_error_response(request, "ROUTE_NOT_FOUND")
    return registered_error_response(
        request,
        "HTTP_REQUEST_ERROR",
        status_code=exception.status_code,
    )


async def validation_exception_handler(
    request: Request,
    _exception: RequestValidationError,
) -> JSONResponse:
    return registered_error_response(request, "INVALID_REQUEST")


async def api_exception_handler(
    request: Request,
    exception: WebApiError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=exception.status_code,
        error_type=exception.error_type,
        code=exception.code,
        message=exception.safe_message,
        retryable=exception.retryable,
    )


async def unexpected_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    error_logger.exception(
        "Unhandled Web backend exception correlation_id=%s",
        request_correlation_id(request),
        exc_info=exception,
    )
    return unexpected_error_response(request)


def register_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(WebApiError, api_exception_handler)
    application.add_exception_handler(Exception, unexpected_exception_handler)
