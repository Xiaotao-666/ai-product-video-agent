"""Public-safe Voice Web preparation and mutation contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VoiceIntent(StrEnum):
    GENERATE = "GENERATE"
    REGENERATE = "REGENERATE"


class VoiceWebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceIssue(VoiceWebModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=300)


class VoicePlannedTiming(VoiceWebModel):
    first_start: float | None = Field(default=None, ge=0)
    last_end: float | None = Field(default=None, ge=0)
    span: float | None = Field(default=None, ge=0)
    narration_duration: float | None = Field(default=None, ge=0)


class VoiceScriptSummary(VoiceWebModel):
    source: str
    text: str
    character_count: int = Field(ge=0)
    cue_count: int = Field(ge=0)


class VoiceProviderOption(VoiceWebModel):
    provider_id: str
    display_name: str
    model: str
    default_voice: str | None = None
    language: str
    supported_languages: list[str] = Field(default_factory=list)
    allowed_voices: list[str] = Field(default_factory=list)
    available: bool


class VoiceOptionsResponse(VoiceWebModel):
    project_id: str
    enabled: bool
    has_active_voice: bool
    active_version: int | None = Field(default=None, ge=1)
    next_version: int = Field(ge=1)
    script: VoiceScriptSummary | None = None
    planned_timing: VoicePlannedTiming
    providers: list[VoiceProviderOption] = Field(default_factory=list)
    default_provider: str | None = None
    default_voice: str | None = None
    default_language: str
    manual_script_required: bool


class VoicePreflightRequest(VoiceWebModel):
    intent: VoiceIntent
    provider: str | None = Field(default=None, max_length=80)
    voice: str = Field(min_length=1, max_length=120)
    language: str = Field(min_length=1, max_length=40)
    script_override: str | None = Field(default=None, max_length=50000)

    @field_validator("provider", "voice", "language", mode="before")
    @classmethod
    def strip_short_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("script_override")
    @classmethod
    def reject_blank_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("script_override cannot be blank")
        return stripped


class VoicePreflightResponse(VoiceWebModel):
    project_id: str
    ready: bool
    intent: VoiceIntent
    next_voice_version: int = Field(ge=1)
    script: VoiceScriptSummary | None = None
    provider: VoiceProviderOption | None = None
    planned_timing: VoicePlannedTiming
    issues: list[VoiceIssue] = Field(default_factory=list)
    warnings: list[VoiceIssue] = Field(default_factory=list)
    external_call_required: bool = True
    external_cost_possible: bool = True
    preflight_fingerprint: str | None = None


class VoiceGenerateRequest(VoicePreflightRequest):
    preflight_fingerprint: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^voice_pf_[0-9a-f]{64}$",
    )
    confirm_external_tts_call: bool


class VoiceTimingAcceptanceRequest(VoiceWebModel):
    expected_voice_version: int = Field(ge=1)
    accepted: bool

    @model_validator(mode="after")
    def require_acceptance(self) -> "VoiceTimingAcceptanceRequest":
        if not self.accepted:
            raise ValueError("accepted must be true")
        return self

