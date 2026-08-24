"""Safe read-only DTOs for Assembly, post-production assets, and Export."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from web_backend.models.assembly_planning import AssemblyPlan


class VoiceCalibrationStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    OUT_OF_TOLERANCE = "OUT_OF_TOLERANCE"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class VoiceTimingAcceptanceDetail(BaseModel):
    accepted: bool
    accepted_at: str | None = None


class AssemblyShotVersion(BaseModel):
    shot_id: int = Field(ge=1)
    video_version: int = Field(ge=1)


class AssemblyFinalVideoSource(BaseModel):
    shot_id: int = Field(ge=1)
    video_version: int = Field(ge=1)
    prompt_version: int | None = Field(default=None, ge=1)
    order: int | None = Field(default=None, ge=1)


class AssemblyFinalVideoVersion(BaseModel):
    final_video_version: int = Field(ge=1)
    assembly_version: int | None = Field(default=None, ge=1)
    created_at: str | None = None
    total_duration: float | None = Field(default=None, ge=0)
    video_available: bool
    is_current: bool
    shots: list[AssemblyFinalVideoSource] = Field(default_factory=list)


class AssemblyDetail(BaseModel):
    project_id: str
    status: str
    current_version: int | None = Field(default=None, ge=1)
    needs_update: bool
    changed_shot_id: int | None = Field(default=None, ge=1)
    created_at: str | None = None
    total_duration: float | None = Field(default=None, ge=0)
    video_available: bool
    shots: list[AssemblyShotVersion] = Field(default_factory=list)
    current_plan: AssemblyPlan | None = None
    final_videos: list[AssemblyFinalVideoVersion] = Field(default_factory=list)


class VoiceDetail(BaseModel):
    project_id: str
    status: str
    version: int | None = Field(default=None, ge=1)
    created_at: str | None = None
    script: str | None = None
    script_source: str | None = None
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    audio_available: bool
    planned_narration_duration: float | None = Field(default=None, ge=0)
    planned_first_voice_start: float | None = Field(default=None, ge=0)
    planned_last_voice_end: float | None = Field(default=None, ge=0)
    planned_voice_span: float | None = Field(default=None, ge=0)
    actual_audio_duration: float | None = Field(default=None, ge=0)
    voice_track_start: float | None = Field(default=None, ge=0)
    actual_voice_end: float | None = Field(default=None, ge=0)
    total_video_duration: float | None = Field(default=None, ge=0)
    duration_difference_seconds: float | None = None
    duration_difference_ratio: float | None = None
    timing_mode: str | None = None
    cue_level_alignment: bool | None = None
    script_matches_storyboard: bool | None = None
    calibration_status: VoiceCalibrationStatus
    timing_acceptance: VoiceTimingAcceptanceDetail | None = None


class VoiceVersionSummary(BaseModel):
    version: int = Field(ge=1)
    created_at: str | None = None
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    script_source: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    calibration_status: VoiceCalibrationStatus
    timing_acceptance: VoiceTimingAcceptanceDetail | None = None
    audio_available: bool
    is_active: bool


class VoiceHistoryResponse(BaseModel):
    project_id: str
    active_version: int | None = Field(default=None, ge=1)
    versions: list[VoiceVersionSummary] = Field(default_factory=list)


class SubtitleCue(BaseModel):
    index: int = Field(ge=1)
    start: str
    end: str
    text: str


class SubtitleDetail(BaseModel):
    project_id: str
    status: str
    version: int | None = Field(default=None, ge=1)
    source: str | None = None
    timing_source: str | None = None
    semantic_type: str | None = None
    source_voice_version: int | None = Field(default=None, ge=1)
    actual_audio_duration: float | None = Field(default=None, ge=0)
    voice_track_start: float | None = Field(default=None, ge=0)
    actual_voice_end: float | None = Field(default=None, ge=0)
    cue_level_alignment: bool | None = None
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    created_at: str | None = None
    cue_count: int = Field(ge=0)
    content_available: bool
    cues: list[SubtitleCue] = Field(default_factory=list)


class SubtitleVersionSummary(BaseModel):
    version: int = Field(ge=1)
    created_at: str | None = None
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    cue_count: int = Field(ge=0)
    source: str | None = None
    timing_source: str | None = None
    semantic_type: str | None = None
    source_voice_version: int | None = Field(default=None, ge=1)
    actual_audio_duration: float | None = Field(default=None, ge=0)
    voice_track_start: float | None = Field(default=None, ge=0)
    actual_voice_end: float | None = Field(default=None, ge=0)
    cue_level_alignment: bool | None = None
    is_active: bool


class SubtitleHistoryResponse(BaseModel):
    project_id: str
    active_version: int | None = Field(default=None, ge=1)
    versions: list[SubtitleVersionSummary] = Field(default_factory=list)


class MusicMixDetail(BaseModel):
    base_volume: float | None = Field(default=None, ge=0, le=1)
    ducking_enabled: bool | None = None
    ducking_ratio: float | None = Field(default=None, ge=0, le=1)
    duck_attack_seconds: float | None = Field(default=None, ge=0)
    duck_release_seconds: float | None = Field(default=None, ge=0)
    fade_in_seconds: float | None = Field(default=None, ge=0)
    fade_out_seconds: float | None = Field(default=None, ge=0)
    loop_music: bool | None = None
    ducking_status: str | None = None


class MusicDetail(BaseModel):
    project_id: str
    status: str
    version: int | None = Field(default=None, ge=1)
    created_at: str | None = None
    audio_available: bool
    format: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    music_mix: MusicMixDetail | None = None


class ExportVoiceTimingSummary(BaseModel):
    timing_mode: str | None = None
    voice_track_start: float | None = Field(default=None, ge=0)
    actual_audio_duration: float | None = Field(default=None, ge=0)
    actual_voice_end: float | None = Field(default=None, ge=0)
    calibration_status: VoiceCalibrationStatus
    cue_level_alignment: bool | None = None


class ExportDetail(BaseModel):
    project_id: str
    status: str
    version: int | None = Field(default=None, ge=1)
    created_at: str | None = None
    stale: bool
    stale_reasons: list[str] = Field(default_factory=list)
    video_available: bool
    assembly_version: int | None = Field(default=None, ge=1)
    voice_version: int | None = Field(default=None, ge=1)
    subtitle_version: int | None = Field(default=None, ge=1)
    music_version: int | None = Field(default=None, ge=1)
    voice_timing: ExportVoiceTimingSummary | None = None
    music_mix: MusicMixDetail | None = None
