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
_SENSITIVE_CORRELATION_ID = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|secret|token)"
)
access_logger = logging.getLogger("uvicorn.error.web_access")


def select_correlation_id(candidate: str | None) -> str:
    if (
        candidate
        and _SAFE_CORRELATION_ID.fullmatch(candidate)
        and not _SENSITIVE_CORRELATION_ID.search(candidate)
    ):
        return candidate
    return f"req_{uuid.uuid4().hex}"


def _route_template(request: Request) -> str:
    """Return the registered route pattern without reflecting path parameters."""

    endpoint = request.scope.get("endpoint")
    for registered_route in getattr(request.app.router, "routes", ()):
        if getattr(registered_route, "endpoint", None) is endpoint:
            registered_path = getattr(registered_route, "path", None)
            if isinstance(registered_path, str) and registered_path.startswith("/"):
                return registered_path
        included_router = getattr(registered_route, "original_router", None)
        include_context = getattr(registered_route, "include_context", None)
        prefix = str(getattr(include_context, "prefix", "") or "").rstrip("/")
        for included_route in getattr(included_router, "routes", ()):
            if getattr(included_route, "endpoint", None) is endpoint:
                included_path = getattr(included_route, "path", None)
                if isinstance(included_path, str) and included_path.startswith("/"):
                    return f"{prefix}{included_path}"

    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str) or not route_path.startswith("/"):
        return "<unmatched>"
    root_path = str(request.scope.get("root_path") or "").rstrip("/")
    if root_path and not route_path.startswith(f"{root_path}/"):
        return f"{root_path}{route_path}"
    return route_path


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
            _route_template(request),
            response.status_code,
            duration_ms,
        )
        return response
