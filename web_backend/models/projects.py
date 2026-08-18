"""Public, path-free DTOs for read-only project APIs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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


class ProjectRequest(ResponseModel):
    product_name: str | None = None
    product_description: str | None = None
    user_notes: str | None = None
    duration_seconds: float | None = None
    video_style: str | None = None
    video_purpose: str | None = None


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
