"""Shared construction of public-safe failures for durable Web tasks."""

from __future__ import annotations

from typing import NoReturn

from web_backend.models.tasks import TaskError
from web_backend.services.task_runner import TaskExecutionFailure


def raise_task_failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> NoReturn:
    """Stop one task callable with an explicitly public-safe error payload."""

    raise TaskExecutionFailure(
        TaskError(code=code, message=message, retryable=retryable)
    )
