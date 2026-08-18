"""Correlation ID and privacy-preserving access logging middleware."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from time import perf_counter

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from web_backend.errors import error_logger, unexpected_error_response


CORRELATION_ID_HEADER = "X-Correlation-ID"
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
access_logger = logging.getLogger("uvicorn.error.web_access")


def select_correlation_id(candidate: str | None) -> str:
    if candidate and _SAFE_CORRELATION_ID.fullmatch(candidate):
        return candidate
    return f"req_{uuid.uuid4().hex}"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a safe request ID and log request metadata without payloads."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        correlation_id = select_correlation_id(
            request.headers.get(CORRELATION_ID_HEADER)
        )
        request.state.correlation_id = correlation_id
        started_at = perf_counter()

        try:
            response = await call_next(request)
        except Exception as exception:
            error_logger.exception(
                "Unhandled Web backend exception correlation_id=%s",
                correlation_id,
                exc_info=exception,
            )
            response = unexpected_error_response(request)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        duration_ms = (perf_counter() - started_at) * 1000
        access_logger.info(
            "timestamp=%s correlation_id=%s method=%s route=%s "
            "status_code=%d duration_ms=%.3f",
            datetime.now(timezone.utc).isoformat(),
            correlation_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
