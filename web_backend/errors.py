"""Safe, consistent HTTP error responses for the Web backend."""

from __future__ import annotations

import logging

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


def unexpected_error_response(request: Request) -> JSONResponse:
    return error_response(
        request,
        status_code=500,
        error_type="INTERNAL_ERROR",
        code="UNEXPECTED_ERROR",
        message="请求处理失败。",
    )


async def http_exception_handler(
    request: Request,
    exception: StarletteHTTPException,
) -> JSONResponse:
    if exception.status_code == 404:
        return error_response(
            request,
            status_code=404,
            error_type="NOT_FOUND",
            code="ROUTE_NOT_FOUND",
            message="请求的资源不存在。",
        )
    return error_response(
        request,
        status_code=exception.status_code,
        error_type="HTTP_ERROR",
        code="HTTP_REQUEST_ERROR",
        message="请求无法处理。",
    )


async def validation_exception_handler(
    request: Request,
    _exception: RequestValidationError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        error_type="VALIDATION_ERROR",
        code="INVALID_REQUEST",
        message="请求参数无效。",
    )


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
