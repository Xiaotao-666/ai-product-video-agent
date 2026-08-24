"""Public-safe contracts for local Music upload and Mix actions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from web_backend.models.postproduction import MusicMixDetail


class MusicWebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicCapabilities(MusicWebModel):
    ducking: bool = True
    fade: bool = True
    loop: bool = False


class MusicOptionsResponse(MusicWebModel):
    project_id: str
    has_music: bool
    active_version: int | None = Field(default=None, ge=1)
    next_version: int = Field(ge=1)
    allowed_extensions: list[str]
    max_file_size_bytes: int = Field(gt=0)
    mix: MusicMixDetail
    capabilities: MusicCapabilities


class MusicMixUpdateRequest(MusicWebModel):
    base_volume: float | None = Field(default=None, ge=0, le=1)
    ducking_enabled: bool | None = None
    ducking_ratio: float | None = Field(default=None, ge=0, le=1)
    duck_attack_seconds: float | None = Field(default=None, ge=0)
    duck_release_seconds: float | None = Field(default=None, ge=0)
    fade_in_seconds: float | None = Field(default=None, ge=0)
    fade_out_seconds: float | None = Field(default=None, ge=0)
    loop_music: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> "MusicMixUpdateRequest":
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Music Mix fields cannot be null")
        return self


class MusicVersionSummary(MusicWebModel):
    version: int = Field(ge=1)
    created_at: str | None = None
    format: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    audio_available: bool
    is_active: bool


class MusicHistoryResponse(MusicWebModel):
    project_id: str
    active_version: int | None = Field(default=None, ge=1)
    versions: list[MusicVersionSummary] = Field(default_factory=list)
