"""Public-safe contracts for Final Export preflight, execution, and history."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from web_backend.models.postproduction import MusicMixDetail


_TOKEN_PATTERN = re.compile(r"^exp_[0-9a-f]{64}$")


class FinalExportWebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinalExportIssue(FinalExportWebModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=500)


class FinalExportInputs(FinalExportWebModel):
    assembly_version: int | None = Field(default=None, ge=1)
    voice_version: int | None = Field(default=None, ge=1)
    subtitle_version: int | None = Field(default=None, ge=1)
    music_version: int | None = Field(default=None, ge=1)


class FinalExportVoiceTiming(FinalExportWebModel):
    status: str
    accepted: bool
    track_start: float | None = Field(default=None, ge=0)
    actual_audio_duration: float | None = Field(default=None, ge=0)
    actual_end: float | None = Field(default=None, ge=0)


class FinalExportSubtitle(FinalExportWebModel):
    semantic_type: str | None = None
    source_voice_version: int | None = Field(default=None, ge=1)
    voice_aligned: bool | None = None


class FinalExportPreflightResponse(FinalExportWebModel):
    project_id: str
    ready: bool
    execution_required: bool
    next_export_version: int = Field(ge=1)
    active_export_version: int | None = Field(default=None, ge=1)
    inputs: FinalExportInputs
    voice_timing: FinalExportVoiceTiming
    subtitle: FinalExportSubtitle
    music_mix: MusicMixDetail | None = None
    existing_export_version: int | None = Field(default=None, ge=1)
    stale: bool
    stale_reasons: list[str] = Field(default_factory=list)
    issues: list[FinalExportIssue] = Field(default_factory=list)
    confirmation_token: str | None = None

    @field_validator("confirmation_token")
    @classmethod
    def validate_token(cls, value: str | None) -> str | None:
        if value is not None and not _TOKEN_PATTERN.fullmatch(value):
            raise ValueError("confirmation token is invalid")
        return value


class FinalExportExecuteRequest(FinalExportWebModel):
    confirmation_token: str = Field(min_length=68, max_length=68)
    confirm_local_export: bool

    @field_validator("confirmation_token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if not _TOKEN_PATTERN.fullmatch(value):
            raise ValueError("confirmation token is invalid")
        return value


class ExportVersionSummary(FinalExportWebModel):
    version: int = Field(ge=1)
    created_at: str | None = None
    assembly_version: int | None = Field(default=None, ge=1)
    voice_version: int | None = Field(default=None, ge=1)
    subtitle_version: int | None = Field(default=None, ge=1)
    music_version: int | None = Field(default=None, ge=1)
    audio_muxed: bool
    subtitle_burned: bool
    duration_seconds: float | None = Field(default=None, ge=0)
    video_available: bool
    is_active: bool
    stale: bool
    stale_reasons: list[str] = Field(default_factory=list)


class ExportVersionDetail(ExportVersionSummary):
    voice_timing: FinalExportVoiceTiming | None = None
    music_mix: MusicMixDetail | None = None


class ExportHistoryResponse(FinalExportWebModel):
    project_id: str
    active_version: int | None = Field(default=None, ge=1)
    versions: list[ExportVersionSummary] = Field(default_factory=list)
