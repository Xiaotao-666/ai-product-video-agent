"""Safe DTOs for local-only Shot generation preparation."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from web_backend.models.projects import ResponseModel


_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ModelSelectionMode(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class GenerationIntent(StrEnum):
    INITIAL = "INITIAL"
    REGENERATE_CURRENT_PROMPT = "REGENERATE_CURRENT_PROMPT"
    REGENERATE_MANUAL_PROMPT = "REGENERATE_MANUAL_PROMPT"
    GENERATE_WITH_PROMPT_VERSION = "GENERATE_WITH_PROMPT_VERSION"


class GenerationVisualInputMode(StrEnum):
    NONE = "none"
    REFERENCE_ASSET = "reference_asset"
    FIRST_FRAME = "first_frame"


class GenerationIssueCode(StrEnum):
    PROMPT_NOT_APPROVED = "PROMPT_NOT_APPROVED"
    SHOT_NOT_READY = "SHOT_NOT_READY"
    SHOT_ALREADY_GENERATED = "SHOT_ALREADY_GENERATED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_VISUAL_INPUT_INCOMPATIBLE = "MODEL_VISUAL_INPUT_INCOMPATIBLE"
    REFERENCE_ASSET_REQUIRED = "REFERENCE_ASSET_REQUIRED"
    REFERENCE_ASSET_NOT_FOUND = "REFERENCE_ASSET_NOT_FOUND"
    REFERENCE_ASSET_INVALID = "REFERENCE_ASSET_INVALID"
    FIRST_FRAME_REQUIRED = "FIRST_FRAME_REQUIRED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    INVALID_DURATION = "INVALID_DURATION"
    INVALID_RESOLUTION = "INVALID_RESOLUTION"
    VISUAL_INPUT_ASSET_NOT_ALLOWED = "VISUAL_INPUT_ASSET_NOT_ALLOWED"
    VISUAL_INPUT_ASSET_COUNT_INVALID = "VISUAL_INPUT_ASSET_COUNT_INVALID"
    PROMPT_EMPTY = "PROMPT_EMPTY"
    PROMPT_INVALID = "PROMPT_INVALID"
    PROMPT_UNCHANGED = "PROMPT_UNCHANGED"
    PROMPT_BASE_STALE = "PROMPT_BASE_STALE"
    PROMPT_VERSION_NOT_FOUND = "PROMPT_VERSION_NOT_FOUND"
    PROMPT_VERSION_NOT_ELIGIBLE = "PROMPT_VERSION_NOT_ELIGIBLE"


class GenerationIssue(ResponseModel):
    code: GenerationIssueCode
    message: str


class GenerationShotContext(ResponseModel):
    shot_id: str
    duration_seconds: int
    prompt_version: int | None
    resolution: str
    official_video_version: int | None = None
    pending_video_version: int | None = None
    next_video_version: int | None = None
    base_video_version: int | None = None
    next_prompt_version: int | None = None
    official_prompt_version: int | None = None
    prompt_source: str | None = None
    prompt_parent_version: int | None = None


class GenerationModelOption(ResponseModel):
    model_id: str
    display_name: str
    provider: str
    provider_display_name: str
    api_version: str
    available: bool
    supported_visual_input_modes: list[GenerationVisualInputMode]
    supported_resolutions: list[str]
    supported_durations: list[int]
    min_duration: int | None = None
    max_duration: int | None = None


class GenerationVisualInputOption(ResponseModel):
    mode: GenerationVisualInputMode
    display_name: str
    description: str
    compatible_model_ids: list[str]


class GenerationOptionsResponse(ResponseModel):
    project_id: str
    eligible: bool
    shot: GenerationShotContext
    selection_modes: list[ModelSelectionMode]
    visual_input_modes: list[GenerationVisualInputOption]
    models: list[GenerationModelOption]
    issues: list[GenerationIssue]
    paid_call_required: bool = True


class ReferenceAssetPublic(ResponseModel):
    asset_id: str
    filename: str
    media_type: str
    width: int
    height: int


class ReferenceAssetUploadResponse(ReferenceAssetPublic):
    deduplicated: bool


class ReferenceAssetListResponse(ResponseModel):
    project_id: str
    assets: list[ReferenceAssetPublic]


class GenerationVisualInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: GenerationVisualInputMode
    asset_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("asset_ids")
    @classmethod
    def validate_asset_ids(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value]
        if any(not _ASSET_ID.fullmatch(item) for item in normalized):
            raise ValueError("asset_id is invalid")
        if len(set(normalized)) != len(normalized):
            raise ValueError("asset_ids must be unique")
        return normalized


class GenerationPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    intent: GenerationIntent = GenerationIntent.INITIAL
    model_selection: ModelSelectionMode
    requested_model: str | None = Field(default=None, max_length=200)
    visual_input: GenerationVisualInputRequest
    base_prompt_version: int | None = Field(default=None, ge=1)
    edited_prompt: str | None = Field(default=None, max_length=12000)
    target_prompt_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_selection(self) -> "GenerationPreflightRequest":
        if self.model_selection is ModelSelectionMode.MANUAL:
            if not self.requested_model:
                raise ValueError("requested_model is required for MANUAL selection")
        elif self.requested_model is not None:
            raise ValueError("requested_model must be null for AUTO selection")
        if self.intent is GenerationIntent.REGENERATE_MANUAL_PROMPT:
            if self.base_prompt_version is None or self.edited_prompt is None:
                raise ValueError(
                    "base_prompt_version and edited_prompt are required for manual Prompt regeneration"
                )
        elif self.base_prompt_version is not None or self.edited_prompt is not None:
            raise ValueError(
                "manual Prompt fields are only valid for manual Prompt regeneration"
            )
        if self.intent is GenerationIntent.GENERATE_WITH_PROMPT_VERSION:
            if self.target_prompt_version is None:
                raise ValueError(
                    "target_prompt_version is required for selected Prompt generation"
                )
        elif self.target_prompt_version is not None:
            raise ValueError(
                "target_prompt_version is only valid for selected Prompt generation"
            )
        return self


class ResolvedGeneration(ResponseModel):
    provider: str
    provider_display_name: str
    model: str
    model_display_name: str
    api_version: str
    generation_mode: str
    generation_mode_display_name: str
    visual_input_mode: GenerationVisualInputMode
    model_selection: ModelSelectionMode


class GenerationPreflightResponse(ResponseModel):
    ready: bool
    shot: GenerationShotContext
    resolved: ResolvedGeneration | None
    provider_available: bool
    selected_asset_ids: list[str]
    issues: list[GenerationIssue]
    warnings: list[GenerationIssue]
    paid_call_required: bool = True
    preflight_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class GenerationStartRequest(GenerationPreflightRequest):
    preflight_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_paid_call: bool


class ShotGenerationState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    SUBMITTING = "SUBMITTING"
    PROVIDER_RUNNING = "PROVIDER_RUNNING"
    READY_TO_DOWNLOAD = "READY_TO_DOWNLOAD"
    DOWNLOADING = "DOWNLOADING"
    LOCAL_FINALIZING = "LOCAL_FINALIZING"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"


class ShotGenerationResumeKind(StrEnum):
    POLL_EXISTING_TASK = "POLL_EXISTING_TASK"
    DOWNLOAD_EXISTING_FILE = "DOWNLOAD_EXISTING_FILE"
    FINALIZE_LOCAL_VIDEO = "FINALIZE_LOCAL_VIDEO"


class ShotGenerationStatusResponse(ResponseModel):
    project_id: str
    shot_id: str
    state: ShotGenerationState
    resume_available: bool
    resume_kind: ShotGenerationResumeKind | None = None
    video_version: int | None = Field(default=None, ge=1)
    prompt_version: int | None = Field(default=None, ge=1)
    provider_submission_known: bool
    generation_intent: GenerationIntent | None = None
