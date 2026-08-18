"""Public, path-free DTOs for read-only project APIs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowPhase(StrEnum):
    CREATIVE = "CREATIVE"
    CREATIVE_REVIEW = "CREATIVE_REVIEW"
    STORYBOARD = "STORYBOARD"
    STORYBOARD_REVIEW = "STORYBOARD_REVIEW"
    VIDEO_PROMPT = "VIDEO_PROMPT"
    VIDEO_PROMPT_REVIEW = "VIDEO_PROMPT_REVIEW"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    SHOT_REVIEW = "SHOT_REVIEW"
    ASSEMBLY = "ASSEMBLY"
    ASSEMBLY_REQUIRED = "ASSEMBLY_REQUIRED"
    POST_PRODUCTION = "POST_PRODUCTION"
    FINAL_EXPORT = "FINAL_EXPORT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class AvailableAction(StrEnum):
    GENERATE_CREATIVE = "GENERATE_CREATIVE"
    APPROVE_CREATIVE = "APPROVE_CREATIVE"
    REVISE_CREATIVE = "REVISE_CREATIVE"
    REGENERATE_CREATIVE = "REGENERATE_CREATIVE"
    GENERATE_STORYBOARD = "GENERATE_STORYBOARD"
    APPROVE_STORYBOARD = "APPROVE_STORYBOARD"
    REVISE_STORYBOARD = "REVISE_STORYBOARD"
    REGENERATE_STORYBOARD = "REGENERATE_STORYBOARD"
    GENERATE_VIDEO_PROMPTS = "GENERATE_VIDEO_PROMPTS"
    APPROVE_VIDEO_PROMPTS = "APPROVE_VIDEO_PROMPTS"
    REVISE_VIDEO_PROMPTS = "REVISE_VIDEO_PROMPTS"
    REGENERATE_VIDEO_PROMPTS = "REGENERATE_VIDEO_PROMPTS"
    GENERATE_SHOTS = "GENERATE_SHOTS"
    REVIEW_SHOTS = "REVIEW_SHOTS"
    MANAGE_SHOT_VERSIONS = "MANAGE_SHOT_VERSIONS"
    ASSEMBLE = "ASSEMBLE"
    GENERATE_VOICE = "GENERATE_VOICE"
    GENERATE_SUBTITLE = "GENERATE_SUBTITLE"
    SET_MUSIC = "SET_MUSIC"
    FINAL_EXPORT = "FINAL_EXPORT"


class StageState(ResponseModel):
    status: str


class ShotStageState(StageState):
    approved: int
    total: int


class AssemblyState(StageState):
    needs_update: bool
    version: int | None = None


class ComponentState(StageState):
    version: int | None = None


class FinalExportState(ComponentState):
    created_at: str | None = None
    stale: bool = False


class WorkflowStages(ResponseModel):
    creative: StageState
    storyboard: StageState
    video_prompt: StageState
    shots: ShotStageState
    assembly: AssemblyState
    voice: ComponentState
    subtitle: ComponentState
    music: ComponentState
    export: FinalExportState


class WorkflowState(ResponseModel):
    workflow_phase: WorkflowPhase
    status: str
    stages: WorkflowStages
    available_actions: list[AvailableAction]


class ProjectRequest(ResponseModel):
    product_name: str | None = None
    product_description: str | None = None
    user_notes: str | None = None
    duration_seconds: float | None = None
    video_style: str | None = None
    video_purpose: str | None = None


class ProjectCreateRequest(BaseModel):
    """HTTP input mirroring the existing Core ProductVideoRequest fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_name: str = Field(max_length=1000)
    product_description: str = Field(max_length=10000)
    user_notes: str = Field(default="", max_length=10000)
    duration_seconds: int
    video_style: str = Field(max_length=2000)
    video_purpose: str = Field(max_length=2000)


class ProjectCreateResponse(ResponseModel):
    project_id: str
    name: str
    workflow_phase: WorkflowPhase
    status: str
    created_at: str
    updated_at: str


class PostProductionState(ResponseModel):
    status: str
    voice: ComponentState
    subtitle: ComponentState
    music: ComponentState


class ProjectSummary(ResponseModel):
    project_id: str
    name: str
    workflow_phase: WorkflowPhase
    status: str
    updated_at: str
    assembly: AssemblyState
    final_export: FinalExportState


class ProjectListResponse(ResponseModel):
    projects: list[ProjectSummary]


class ProjectDetail(ResponseModel):
    project_id: str
    name: str
    request: ProjectRequest
    workflow: WorkflowState
    assembly: AssemblyState
    post_production: PostProductionState
    final_export: FinalExportState
    updated_at: str


class ProjectWorkflowResponse(ResponseModel):
    project_id: str
    workflow_phase: WorkflowPhase
    status: str
    stages: WorkflowStages
    available_actions: list[AvailableAction]
    updated_at: str
