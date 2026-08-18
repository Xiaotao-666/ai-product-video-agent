"""Public DTOs for read-only planning content APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from web_backend.models.projects import ResponseModel


class CreativeReviseRequest(BaseModel):
    """Bounded revision feedback; unknown fields are never accepted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feedback: str = Field(min_length=1, max_length=4000)


class CreativeNarrationPlan(ResponseModel):
    enabled: bool
    tone: str | None = None
    full_script: str | None = None
    target_duration_seconds: float | None = None


class CreativeSubtitleStrategy(ResponseModel):
    enabled: bool
    tone: str | None = None
    density: str | None = None
    max_lines: int | None = None
    preferred_position: str | None = None
    principles: list[str]


class CreativeGlobalConstraints(ResponseModel):
    must: list[str]
    must_not: list[str]


class CreativeForbiddenWindow(ResponseModel):
    start: float | None = None
    end: float | None = None
    tracks: list[str]


class CreativeAVTimelineConstraints(ResponseModel):
    forbidden_windows: list[CreativeForbiddenWindow]


class CreativePlanningContent(ResponseModel):
    creative_concept: str | None = None
    target_audience: str | None = None
    key_message: str | None = None
    visual_direction: str | None = None
    narrative_arc: str | None = None
    narration_plan: CreativeNarrationPlan
    subtitle_strategy: CreativeSubtitleStrategy
    global_constraints: CreativeGlobalConstraints
    av_timeline_constraints: CreativeAVTimelineConstraints


class CreativeContentResponse(ResponseModel):
    project_id: str
    status: str
    content: CreativePlanningContent | None = None


class PlanningCue(ResponseModel):
    text: str | None = None
    start_offset: float | None = None
    end_offset: float | None = None
    position: str | None = None


class StoryboardVideoConstraints(ResponseModel):
    reserve_subtitle_space: bool
    subtitle_safe_area: str | None = None


class StoryboardShotContent(ResponseModel):
    shot_id: int | None = None
    duration_seconds: float | None = None
    purpose: str | None = None
    visual: str | None = None
    camera: str | None = None
    voiceover_cues: list[PlanningCue]
    subtitle_cues: list[PlanningCue]
    video_constraints: StoryboardVideoConstraints


class StoryboardPlanningContent(ResponseModel):
    total_duration_seconds: float | None = None
    shots: list[StoryboardShotContent]


class StoryboardContentResponse(ResponseModel):
    project_id: str
    status: str
    content: StoryboardPlanningContent | None = None


class VideoPromptShotContent(ResponseModel):
    shot_id: int | None = None
    prompt_version: int | None = None
    prompt_source: str | None = None
    visual_prompt_core: str | None = None
    prompt_text: str | None = None


class VideoPromptPlanningContent(ResponseModel):
    shots: list[VideoPromptShotContent]


class VideoPromptsContentResponse(ResponseModel):
    project_id: str
    status: str
    content: VideoPromptPlanningContent | None = None
