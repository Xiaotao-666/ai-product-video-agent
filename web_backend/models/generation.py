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


class GenerationIssue(ResponseModel):
    code: GenerationIssueCode
    message: str


class GenerationShotContext(ResponseModel):
    shot_id: str
    duration_seconds: int
    prompt_version: int | None
    resolution: str


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

    model_selection: ModelSelectionMode
    requested_model: str | None = Field(default=None, max_length=200)
    visual_input: GenerationVisualInputRequest

    @model_validator(mode="after")
    def validate_selection(self) -> "GenerationPreflightRequest":
        if self.model_selection is ModelSelectionMode.MANUAL:
            if not self.requested_model:
                raise ValueError("requested_model is required for MANUAL selection")
        elif self.requested_model is not None:
            raise ValueError("requested_model must be null for AUTO selection")
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

