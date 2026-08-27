"""Path-free recovery decisions and explicit paid failed-retry requests."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from web_backend.models.generation import (
    GenerationOptionsResponse, GenerationPreflightResponse,
    GenerationVisualInputRequest, ModelSelectionMode,
)
from web_backend.models.projects import ResponseModel


class FailureRecoveryState(StrEnum):
    RETRY_ALLOWED = "RETRY_ALLOWED"
    RETRY_BLOCKED_SUBMISSION_UNKNOWN = "RETRY_BLOCKED_SUBMISSION_UNKNOWN"
    RESUME_AVAILABLE = "RESUME_AVAILABLE"
    BUSINESS_ALREADY_COMPLETE = "BUSINESS_ALREADY_COMPLETE"
    ACTIVE_TASK = "ACTIVE_TASK"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FailureRecovery(ResponseModel):
    state: FailureRecoveryState
    reason_code: str
    can_retry: bool = False
    requires_new_preflight: bool = False
    requires_external_cost_confirmation: bool = False
    safe_message: str
    last_attempt_version: int | None = None
    active_task_id: str | None = None


class FailedRetryPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    intent: Literal["FAILED_RETRY"] = "FAILED_RETRY"
    model_selection: ModelSelectionMode
    requested_model: str | None = Field(default=None, max_length=200)
    duration: int = Field(strict=True, ge=1, le=600)
    resolution: str = Field(pattern=r"^[0-9]{1,4}[PK]$")
    visual_input: GenerationVisualInputRequest

    @model_validator(mode="after")
    def selection(self) -> "FailedRetryPreflightRequest":
        if self.model_selection is ModelSelectionMode.MANUAL:
            if not self.requested_model:
                raise ValueError("manual selection requires a model")
        elif self.requested_model is not None:
            raise ValueError("automatic selection cannot specify a model")
        return self


class FailedRetryRequest(FailedRetryPreflightRequest):
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_external_video_call: StrictBool = False


class FailedRetryOptions(GenerationOptionsResponse):
    failure_recovery: FailureRecovery


class FailedRetryPreflight(GenerationPreflightResponse):
    intent: Literal["FAILED_RETRY"] = "FAILED_RETRY"
    failure_recovery: FailureRecovery
