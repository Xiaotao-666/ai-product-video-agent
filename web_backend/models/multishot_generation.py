"""Public DTOs for project-level multi-Shot generation orchestration."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from web_backend.models.projects import ResponseModel
from web_backend.models.tasks import TaskStatus


class MultiShotPlanStatus(StrEnum):
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    PARTIAL_PROGRESS = "PARTIAL_PROGRESS"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    NOT_STARTED = "NOT_STARTED"


class MultiShotGenerationOption(ResponseModel):
    shot_id: str
    order: int = Field(ge=1)
    title: str
    status: str
    prompt_ready: bool
    video_status: str
    available: bool


class MultiShotGenerationAggregation(ResponseModel):
    total: int = Field(ge=0)
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    waiting_review: int = Field(ge=0)
    approved: int = Field(ge=0)
    failed: int = Field(ge=0)
    not_started: int = Field(ge=0)


class MultiShotGenerationOptionsResponse(ResponseModel):
    project_id: str
    status: MultiShotPlanStatus
    max_parallel: int = Field(ge=1)
    aggregation: MultiShotGenerationAggregation
    shots: list[MultiShotGenerationOption]


class MultiShotGenerationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    shots: list[str] = Field(min_length=1, max_length=100)
    confirm_paid_call: bool

    @field_validator("shots")
    @classmethod
    def require_unique_shots(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("shots must be unique")
        return value


class MultiShotGenerationPlanItem(ResponseModel):
    shot_id: str
    task_id: str
    operation: str
    status: TaskStatus


class MultiShotGenerationPlanResponse(ResponseModel):
    project_id: str
    status: MultiShotPlanStatus
    max_parallel: int = Field(ge=1)
    shots: list[MultiShotGenerationPlanItem]
    aggregation: MultiShotGenerationAggregation
