"""Public-safe contracts for deterministic local Subtitle Web actions."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SubtitleSourceType(StrEnum):
    ACTIVE_VOICE = "active_voice"


class SubtitleWebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubtitleIssue(SubtitleWebModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=300)


class SubtitleSourceSummary(SubtitleWebModel):
    type: SubtitleSourceType
    label: str
    cue_count: int = Field(ge=0)
    timing_source: str
    voice_version: int | None = Field(default=None, ge=1)
    semantic_type: str
    script: str
    actual_audio_duration: float = Field(ge=0)
    voice_track_start: float = Field(ge=0)
    actual_voice_end: float = Field(ge=0)
    cue_level_alignment: bool


class SubtitleOptionsResponse(SubtitleWebModel):
    project_id: str
    applicable: bool
    ready: bool
    stale: bool
    stale_reason: str | None = None
    active_version: int | None = Field(default=None, ge=1)
    next_version: int = Field(ge=1)
    source: SubtitleSourceSummary | None = None
    issues: list[SubtitleIssue] = Field(default_factory=list)


class SubtitleGenerateRequest(SubtitleWebModel):
    expected_active_version: int | None = Field(default=None, ge=1)
    expected_next_version: int = Field(ge=1)
    expected_voice_version: int = Field(ge=1)
