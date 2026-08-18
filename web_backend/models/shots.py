"""Public, path-free DTOs for read-only Shot browsing."""

from __future__ import annotations

from enum import StrEnum

from web_backend.models.projects import ResponseModel


class ShotVersionRole(StrEnum):
    OFFICIAL = "OFFICIAL"
    PENDING_REVIEW = "PENDING_REVIEW"
    HISTORY = "HISTORY"


class ShotVisualInputMode(StrEnum):
    NONE = "NONE"
    FIRST_FRAME = "FIRST_FRAME"
    REFERENCE_ASSET = "REFERENCE_ASSET"
    UNKNOWN = "UNKNOWN"


class ShotSummary(ResponseModel):
    shot_id: str
    status: str
    official_version: int | None = None
    pending_review_version: int | None = None
    version_count: int
    generation_count: int


class ShotListResponse(ResponseModel):
    project_id: str
    status: str
    shots: list[ShotSummary]


class ShotPromptSummary(ResponseModel):
    version: int | None = None
    source: str | None = None
    visual_prompt_core: str | None = None
    final_prompt: str | None = None


class ShotGenerationSummary(ResponseModel):
    model: str | None = None
    visual_input_mode: ShotVisualInputMode


class ShotVersion(ResponseModel):
    version: int
    role: ShotVersionRole
    review_status: str
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
    versions: list[ShotVersion]
