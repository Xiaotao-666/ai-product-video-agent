"""Public, path-free DTOs for read-only Shot browsing."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from web_backend.models.projects import ResponseModel


class ShotVersionRole(StrEnum):
    OFFICIAL = "OFFICIAL"
    PENDING_REVIEW = "PENDING_REVIEW"
    HISTORY = "HISTORY"


class ShotVersionHistoryReason(StrEnum):
    PREVIOUSLY_APPROVED = "PREVIOUSLY_APPROVED"
    SUPERSEDED = "SUPERSEDED"
    EXPLICITLY_REJECTED = "EXPLICITLY_REJECTED"
    UNKNOWN = "UNKNOWN"


class ShotVisualInputMode(StrEnum):
    NONE = "NONE"
    FIRST_FRAME = "FIRST_FRAME"
    REFERENCE_ASSET = "REFERENCE_ASSET"
    UNKNOWN = "UNKNOWN"


class ShotSummary(ResponseModel):
    shot_id: str
    order: int = Field(ge=1)
    title: str
    status: str
    prompt_status: str
    video_status: str
    review_status: str
    official_version: int | None = None
    pending_review_version: int | None = None
    version_count: int
    generation_count: int


class ShotStatusAggregation(ResponseModel):
    total: int = Field(ge=0)
    approved: int = Field(ge=0)
    waiting_review: int = Field(ge=0)
    generating: int = Field(ge=0)
    not_started: int = Field(ge=0)
    failed: int = Field(ge=0)


class ShotListResponse(ResponseModel):
    project_id: str
    status: str
    aggregation: ShotStatusAggregation
    shots: list[ShotSummary]


class ShotPromptSummary(ResponseModel):
    version: int | None = None
    source: str | None = None
    visual_prompt_core: str | None = None
    final_prompt: str | None = None


class ShotPromptVersionSummary(ResponseModel):
    version: int
    source: str | None = None
    parent_version: int | None = None
    created_at: str | None = None


class ShotGenerationSummary(ResponseModel):
    model: str | None = None
    visual_input_mode: ShotVisualInputMode


class ShotVersion(ResponseModel):
    version: int
    role: ShotVersionRole
    review_status: str
    history_reason: ShotVersionHistoryReason | None = None
    created_at: str | None = None
    prompt: ShotPromptSummary
    generation: ShotGenerationSummary
    video_available: bool


class ShotDetail(ResponseModel):
    project_id: str
    shot_id: str
    status: str
    official_version: int | None = None
    pending_review_version: int | None = None
    version_count: int
    generation_count: int
    active_prompt_version: int | None = None
    approved_prompt_version: int | None = None
    prompt_versions: list[ShotPromptVersionSummary]
    versions: list[ShotVersion]
