"""Durable, public-safe models for local Web task tracking."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{32}$")
_SAFE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SENSITIVE_CORRELATION_ID = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer|secret|token)"
)
_UNSAFE_PUBLIC_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|file://|api[_ -]?key|credential|authorization|"
    r"provider secret|bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)


class TaskStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


ACTIVE_TASK_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.INTERRUPTED,
        TaskStatus.CANCELLED,
    }
)


class TaskOperation(StrEnum):
    CREATIVE_GENERATE = "CREATIVE_GENERATE"
    CREATIVE_RETRY = "CREATIVE_RETRY"
    CREATIVE_REVISE = "CREATIVE_REVISE"
    CREATIVE_REGENERATE = "CREATIVE_REGENERATE"
    STORYBOARD_GENERATE = "STORYBOARD_GENERATE"
    STORYBOARD_REVISE = "STORYBOARD_REVISE"
    STORYBOARD_REGENERATE = "STORYBOARD_REGENERATE"
    VIDEO_PROMPT_GENERATE = "VIDEO_PROMPT_GENERATE"
    VIDEO_PROMPT_REVISE = "VIDEO_PROMPT_REVISE"
    VIDEO_PROMPT_REGENERATE = "VIDEO_PROMPT_REGENERATE"
    SHOT_GENERATE = "SHOT_GENERATE"
    SHOT_RESUME = "SHOT_RESUME"
    ASSEMBLY = "ASSEMBLY"
    VOICE_GENERATE = "VOICE_GENERATE"
    SUBTITLE_GENERATE = "SUBTITLE_GENERATE"
    FINAL_EXPORT = "FINAL_EXPORT"


class TaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskError(TaskModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not _SAFE_CODE_PATTERN.fullmatch(value):
            raise ValueError("task error code is unsafe")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if _UNSAFE_PUBLIC_TEXT.search(value):
            raise ValueError("task error message is unsafe")
        return value


class TaskResultReference(TaskModel):
    """Small result pointer; business state remains in Core persistence."""

    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    version: int | None = Field(default=None, ge=1)

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, value: str) -> str:
        if not _SAFE_CODE_PATTERN.fullmatch(value):
            raise ValueError("task result resource type is unsafe")
        return value

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not _SAFE_REFERENCE_PATTERN.fullmatch(value)
            or _UNSAFE_PUBLIC_TEXT.search(value)
        ):
            raise ValueError("task result resource id is unsafe")
        return value


class TaskRecord(TaskModel):
    task_id: str = Field(pattern=TASK_ID_PATTERN.pattern)
    project_id: str = Field(min_length=1, max_length=255)
    operation: TaskOperation
    target_id: str | None = Field(default=None, max_length=128)
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    correlation_id: str = Field(min_length=1, max_length=64)
    error: TaskError | None = None
    result: TaskResultReference | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("task timestamps must include a timezone")
        return value

    @field_validator("correlation_id")
    @classmethod
    def validate_correlation_id(cls, value: str) -> str:
        if (
            not _SAFE_CORRELATION_ID.fullmatch(value)
            or _SENSITIVE_CORRELATION_ID.search(value)
            or _UNSAFE_PUBLIC_TEXT.search(value)
        ):
            raise ValueError("task correlation id is unsafe")
        return value

    @field_validator("target_id")
    @classmethod
    def validate_target_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not _SAFE_REFERENCE_PATTERN.fullmatch(value)
            or _UNSAFE_PUBLIC_TEXT.search(value)
        ):
            raise ValueError("task target id is unsafe")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "TaskRecord":
        if self.status is TaskStatus.QUEUED:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("queued task cannot have execution timestamps")
        elif self.status is TaskStatus.RUNNING:
            if self.started_at is None or self.finished_at is not None:
                raise ValueError("running task timestamps are inconsistent")
        elif self.status in TERMINAL_TASK_STATUSES and self.finished_at is None:
            raise ValueError("terminal task requires finished_at")

        if self.status in {TaskStatus.FAILED, TaskStatus.INTERRUPTED}:
            if self.error is None:
                raise ValueError("failed or interrupted task requires safe error")
        elif self.error is not None:
            raise ValueError("task error is only valid for failed/interrupted tasks")

        if self.result is not None and self.status is not TaskStatus.SUCCEEDED:
            raise ValueError("task result is only valid for succeeded tasks")
        return self


class ProjectTaskListResponse(TaskModel):
    project_id: str
    tasks: list[TaskRecord] = Field(default_factory=list)
